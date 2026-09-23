use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

/// Cache line size for modern x86_64 / ARM64 processors.
const CACHE_LINE_SIZE: usize = 64;

/// Cache-line padded atomic counter to eliminate false sharing between cores.
#[repr(align(64))]
struct CachePaddedCounter {
    value: AtomicUsize,
    _pad: [u8; CACHE_LINE_SIZE - std::mem::size_of::<AtomicUsize>()],
}

impl CachePaddedCounter {
    const fn new(val: usize) -> Self {
        Self {
            value: AtomicUsize::new(val),
            _pad: [0u8; CACHE_LINE_SIZE - std::mem::size_of::<AtomicUsize>()],
        }
    }
}

/// Ultra-low-latency Lock-Free Single-Producer Single-Consumer (SPSC) Ring Buffer.
/// Capacity must be a power of two for branchless index wrapping via bitwise AND.
pub struct SpscRingBuffer<T, const CAP: usize> {
    buffer: Box<[std::mem::MaybeUninit<T>; CAP]>,
    head: CachePaddedCounter, // Written by Producer
    tail: CachePaddedCounter, // Written by Consumer
}

// Compile-time check that CAP is power of two
const fn is_power_of_two(n: usize) -> bool {
    n > 0 && (n & (n - 1)) == 0
}

impl<T, const CAP: usize> SpscRingBuffer<T, CAP> {
    pub fn new() -> Arc<Self> {
        assert!(is_power_of_two(CAP), "Ring buffer capacity must be a power of 2");

        // Safety: uninitialized array of MaybeUninit is valid memory
        let buffer = unsafe {
            Box::new(std::mem::MaybeUninit::uninit().assume_init())
        };

        Arc::new(Self {
            buffer,
            head: CachePaddedCounter::new(0),
            tail: CachePaddedCounter::new(0),
        })
    }

    /// Push an item into the ring buffer. Called exclusively by Producer thread.
    /// Returns Err(item) if the ring buffer is full.
    #[inline(always)]
    pub fn push(&self, item: T) -> Result<(), T> {
        let head = self.head.value.load(Ordering::Relaxed);
        let tail = self.tail.value.load(Ordering::Acquire);

        if head.wrapping_sub(tail) >= CAP {
            return Err(item); // Buffer full
        }

        let mask = CAP - 1;
        let idx = head & mask;

        // Safety: head & mask is within [0, CAP-1] and slot is free
        unsafe {
            let slot = self.buffer.as_ptr().add(idx) as *mut std::mem::MaybeUninit<T>;
            (*slot).write(item);
        }

        // Release order ensures the memory write is visible before head is updated
        self.head.value.store(head.wrapping_add(1), Ordering::Release);
        Ok(())
    }

    /// Pop an item from the ring buffer. Called exclusively by Consumer thread.
    /// Returns None if the ring buffer is empty.
    #[inline(always)]
    pub fn pop(&self) -> Option<T> {
        let tail = self.tail.value.load(Ordering::Relaxed);
        let head = self.head.value.load(Ordering::Acquire);

        if tail == head {
            return None; // Buffer empty
        }

        let mask = CAP - 1;
        let idx = tail & mask;

        // Safety: tail & mask is within [0, CAP-1] and slot was written
        let item = unsafe {
            let slot = self.buffer.as_ptr().add(idx) as *mut std::mem::MaybeUninit<T>;
            (*slot).assume_init_read()
        };

        // Release order ensures item read before updating tail
        self.tail.value.store(tail.wrapping_add(1), Ordering::Release);
        Some(item)
    }

    #[inline(always)]
    pub fn is_empty(&self) -> bool {
        self.tail.value.load(Ordering::Relaxed) == self.head.value.load(Ordering::Relaxed)
    }

    #[inline(always)]
    pub fn len(&self) -> usize {
        let head = self.head.value.load(Ordering::Relaxed);
        let tail = self.tail.value.load(Ordering::Relaxed);
        head.wrapping_sub(tail)
    }
}

// Safety: SpscRingBuffer can be shared between threads if T: Send
unsafe impl<T: Send, const CAP: usize> Send for SpscRingBuffer<T, CAP> {}
unsafe impl<T: Send, const CAP: usize> Sync for SpscRingBuffer<T, CAP> {}
