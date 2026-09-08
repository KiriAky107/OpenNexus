//! C23 compatibility for the MSVCRT-based Windows GNU development target.
//! Recent libsodium archives reference memset_explicit, absent in MSVCRT.
//! Volatile stores preserve its non-elidable wipe semantics; MSVC/UCRT release
//! builds use their native runtime and do not compile this compatibility symbol.

#[cfg(all(windows, target_env = "gnu"))]
#[no_mangle]
unsafe extern "C" fn memset_explicit(
    destination: *mut std::ffi::c_void,
    value: std::ffi::c_int,
    count: usize,
) -> *mut std::ffi::c_void {
    for offset in 0..count {
        // SAFETY: the C ABI caller must supply a writable region of count bytes,
        // exactly as for memset. Volatile stores cannot be removed as dead writes.
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
        // SAFETY: the subslice contains exactly 32 writable bytes.
        assert_eq!(unsafe { super::memset_explicit(pointer, 0, 32) }, pointer);
        assert_eq!(data[0], 0x55);
        assert_eq!(data[33], 0x55);
        assert!(data[1..33].iter().all(|b| *b == 0));
    }
}
