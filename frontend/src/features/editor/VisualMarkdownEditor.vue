<script setup lang="ts">
import { ref } from 'vue'
import RichMarkdownEditor from './RichMarkdownEditor.vue'
import type { RichEditorPlatform } from './richEditorPlatform'
import { useEditorStore } from '@/stores/editor'
import { useSettingsStore } from '@/stores/settings'
import { useThemeStore } from '@/stores/theme'
import { useLayoutPreferencesStore } from '@/stores/layoutPreferences'
import { useHeadingAppearanceStore } from '@/stores/headingAppearance'
import { useMarkdownPreferencesStore } from '@/stores/markdownPreferences'
import { executeEditorCommand, registerEditorCommands } from '@/services/editorCommandService'
import { loadWorkspaceImage, resolveWorkspaceAssetPath, storeWorkspaceImage } from '@/services/workspaceService'
import { navigateMarkdownHref } from '@/services/markdownLinkService'
defineProps<{initialContent:string}>()
const surface=ref<InstanceType<typeof RichMarkdownEditor>>()
const platform:RichEditorPlatform={document:useEditorStore(),settings:useSettingsStore(),theme:useThemeStore(),layout:useLayoutPreferencesStore(),headings:useHeadingAppearanceStore(),markdown:{...useMarkdownPreferencesStore().normalized},assets:{resolvePath:resolveWorkspaceAssetPath,read:loadWorkspaceImage,store:storeWorkspaceImage,remoteImages:true},commands:{register:registerEditorCommands,execute:executeEditorCommand},openLink:navigateMarkdownHref}
defineExpose({getEditor:()=>surface.value?.getEditor()})
</script>
<template><RichMarkdownEditor ref="surface" :initial-content="initialContent" :platform="platform" /></template>
