import java.util.Properties
import java.security.MessageDigest

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("rust")
}

val tauriProperties = Properties().apply {
    val propFile = file("tauri.properties")
    if (propFile.exists()) {
        propFile.inputStream().use { load(it) }
    }
}

val platformVerifierVersion = "0.2.0"
val platformVerifierArtifacts = mapOf(
    "aar" to "aa021794230fbc2f0be355999e2cf67398dd563de066a2e17001ffbd0b69101b",
    "pom" to "78dcb69a1a20e91e72017de9a67104bba1874a7ecc8e6dcdfb2fe1a85de6b362"
)
val platformVerifierLock = file("../../../Cargo.lock").readLines()
    .dropWhile { it.trim() != "name = \"rustls-platform-verifier-android\"" }
    .drop(1).firstOrNull()?.trim()
check(platformVerifierLock == "version = \"$platformVerifierVersion\"") {
    "The Android TLS JVM component must match Cargo.lock"
}
repositories {
    exclusiveContent {
        forRepository {
            maven {
                name = "rustlsPlatformVerifier"
                // Fixed upstream archive commit; keep the JVM artifact and
                // Rust dependency in the same reviewed compatibility set.
                url = uri("https://raw.githubusercontent.com/rustls/rustls-platform-verifier/1aa691352a5e0c215210dfc9a3c2f2078639dc91/android-release-support/maven/")
            }
        }
        filter { includeModule("org.rustls", "rustls-platform-verifier") }
    }
}

android {
    compileSdk = 36
    ndkVersion = "30.0.16248370"
    namespace = "cc.kronecker.opennexus"
    defaultConfig {
        manifestPlaceholders["usesCleartextTraffic"] = "false"
        applicationId = "cc.kronecker.opennexus"
        minSdk = 24
        targetSdk = 36
        versionCode = tauriProperties.getProperty("tauri.android.versionCode", "1").toInt()
        versionName = tauriProperties.getProperty("tauri.android.versionName", "1.0")
    }
    buildTypes {
        getByName("debug") {
            applicationIdSuffix = ".dev"
            manifestPlaceholders["usesCleartextTraffic"] = "true"
            isDebuggable = true
            isJniDebuggable = true
            isMinifyEnabled = false
            packaging {                jniLibs.keepDebugSymbols.add("*/arm64-v8a/*.so")
                jniLibs.keepDebugSymbols.add("*/armeabi-v7a/*.so")
                jniLibs.keepDebugSymbols.add("*/x86/*.so")
                jniLibs.keepDebugSymbols.add("*/x86_64/*.so")
            }
        }
        getByName("release") {
            isMinifyEnabled = true
            proguardFiles(
                *fileTree(".") { include("**/*.pro") }
                    .plus(getDefaultProguardFile("proguard-android-optimize.txt"))
                    .toList().toTypedArray()
            )
        }
    }
    kotlinOptions {
        jvmTarget = "1.8"
    }
    buildFeatures {
        buildConfig = true
    }
}

rust {
    rootDirRel = "../../../"
}

dependencies {
    implementation("org.rustls:rustls-platform-verifier:$platformVerifierVersion") {
        version { strictly(platformVerifierVersion) }
    }
    implementation("androidx.webkit:webkit:1.14.0")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.lifecycle:lifecycle-process:2.10.0")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.1.4")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.5.0")
}

val verifyPlatformVerifierOrigin = tasks.register("verifyPlatformVerifierOrigin") {
    doLast {
        val artifacts = platformVerifierArtifacts.map { (extension, expected) ->
            val dependency = dependencies.create("org.rustls:rustls-platform-verifier:$platformVerifierVersion@$extension")
            val configuration = configurations.detachedConfiguration(dependency).apply { isTransitive = false }
            val artifact = configuration.singleFile
            val sha256 = artifact.inputStream().use { input ->
                val digest = MessageDigest.getInstance("SHA-256")
                val buffer = ByteArray(8192)
                while (true) { val count = input.read(buffer); if (count < 0) break; digest.update(buffer, 0, count) }
                digest.digest().joinToString("") { "%02x".format(it.toInt() and 0xff) }
            }
            check(sha256 == expected) { "Android TLS JVM artifact differs from its reviewed upstream Git bytes" }
            mapOf("extension" to extension, "bytes" to artifact.length(), "sha256" to sha256)
        }
        val report = layout.buildDirectory.file("reports/platform-verifier/origin.json").get().asFile
        report.parentFile.mkdirs()
        report.writeText(groovy.json.JsonOutput.toJson(mapOf(
            "coordinate" to "org.rustls:rustls-platform-verifier:$platformVerifierVersion",
            "upstream_commit" to "1aa691352a5e0c215210dfc9a3c2f2078639dc91",
            "artifacts" to artifacts
        )) + "\n")
    }
}
tasks.named("preBuild").configure { dependsOn(verifyPlatformVerifierOrigin) }

apply(from = "tauri.build.gradle.kts")
