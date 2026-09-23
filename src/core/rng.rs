/// High-speed, zero-dependency XorShift64* Pseudo-Random Number Generator.
/// Generates uniform pseudo-random 64-bit numbers in ~1-2 nanoseconds.
pub struct FastRng {
    state: u64,
}

impl FastRng {
    pub const fn new(seed: u64) -> Self {
        Self {
            state: if seed == 0 { 0x853c49e6748fea9b } else { seed },
        }
    }

    #[inline(always)]
    pub fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545F4914F6CDD1D)
    }

    #[inline(always)]
    pub fn gen_range_f64(&mut self, min: f64, max: f64) -> f64 {
        let norm = (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64;
        min + norm * (max - min)
    }

    #[inline(always)]
    pub fn gen_range_u32(&mut self, min: u32, max: u32) -> u32 {
        let diff = max - min;
        if diff == 0 {
            min
        } else {
            min + (self.next_u64() % (diff as u64)) as u32
        }
    }

    #[inline(always)]
    pub fn gen_bool(&mut self, probability: f64) -> bool {
        self.gen_range_f64(0.0, 1.0) < probability
    }
}
