/// Zero-allocation, cache-friendly object arena with an intrusive free-list.
/// Pre-allocates all slots upfront so that allocations during the critical path are pure O(1) index updates.
pub struct Arena<T: Default> {
    storage: Vec<T>,
    free_indices: Vec<u32>,
    allocated_count: usize,
}

impl<T: Default> Arena<T> {
    pub fn with_capacity(capacity: usize) -> Self {
        let mut storage = Vec::with_capacity(capacity);
        for _ in 0..capacity {
            storage.push(T::default());
        }

        let mut free_indices = Vec::with_capacity(capacity);
        for i in (0..capacity as u32).rev() {
            free_indices.push(i);
        }

        Self {
            storage,
            free_indices,
            allocated_count: 0,
        }
    }

    #[inline(always)]
    pub fn alloc(&mut self) -> Option<u32> {
        if let Some(idx) = self.free_indices.pop() {
            self.allocated_count += 1;
            Some(idx)
        } else {
            None // Out of pre-allocated capacity; critical path does not dynamically resize
        }
    }

    #[inline(always)]
    pub fn dealloc(&mut self, idx: u32) {
        debug_assert!((idx as usize) < self.storage.len());
        self.free_indices.push(idx);
        self.allocated_count = self.allocated_count.saturating_sub(1);
    }

    #[inline(always)]
    pub fn get(&self, idx: u32) -> &T {
        &self.storage[idx as usize]
    }

    #[inline(always)]
    pub fn get_mut(&mut self, idx: u32) -> &mut T {
        &mut self.storage[idx as usize]
    }

    #[inline(always)]
    pub fn capacity(&self) -> usize {
        self.storage.len()
    }

    #[inline(always)]
    pub fn active_count(&self) -> usize {
        self.allocated_count
    }

    #[inline(always)]
    pub fn remaining_capacity(&self) -> usize {
        self.free_indices.len()
    }

    pub fn clear(&mut self) {
        self.free_indices.clear();
        for i in (0..self.storage.len() as u32).rev() {
            self.free_indices.push(i);
        }
        self.allocated_count = 0;
    }
}
