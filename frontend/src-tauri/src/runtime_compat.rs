//! C23 与基于 MSVCRT 的 Windows GNU 开发目标的兼容性。最近的 libsodium 档案参考 memset_explicit，MSVCRT 中不存在。易失性存储保留其不可消除的擦除语义； MSVC/UCRT 发行版本使用其本机运行时，并且不编译此兼容性符号。

#[cfg(all(windows, target_env = "gnu"))]
#[no_mangle]
unsafe extern "C" fn memset_explicit(
    destination: *mut std::ffi::c_void,
    value: std::ffi::c_int,
    count: usize,
) -> *mut std::ffi::c_void {
    for offset in 0..count {
        // SAFETY：C ABI 调用者必须提供 count 字节的可写区域，与 memset 完全相同。易失性存储无法作为死写删除。
        unsafe {
            destination
                .cast::<u8>()
                .add(offset)
                .write_volatile(value as u8);
        }
    }
    std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
    destination
}

#[cfg(all(test, windows, target_env = "gnu"))]
mod tests {
    #[test]
    fn explicit_memset_preserves_surrounding_bytes_and_return_pointer() {
        let mut data = [0x55u8; 34];
        let pointer = data[1..33].as_mut_ptr().cast();
        // SAFETY: 子片恰好包含 32 个可写字节。
        assert_eq!(unsafe { super::memset_explicit(pointer, 0, 32) }, pointer);
        assert_eq!(data[0], 0x55);
        assert_eq!(data[33], 0x55);
        assert!(data[1..33].iter().all(|b| *b == 0));
    }
}
