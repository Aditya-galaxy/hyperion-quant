use crate::orderbook::lob::LimitOrderBook;

/// Order Flow Imbalance (OFI) Alpha Model.
/// Estimates short-term price impact and flow toxicity to skew market making quotes.
pub struct OfiAlpha {
    pub alpha_multiplier: f64,
    pub max_skew_price: f64,
    pub decay_factor: f64,
    smoothed_ofi: f64,
}

impl OfiAlpha {
    pub fn new(alpha_multiplier: f64, max_skew_price: f64, decay_factor: f64) -> Self {
        Self {
            alpha_multiplier,
            max_skew_price,
            decay_factor,
            smoothed_ofi: 0.0,
        }
    }

    /// Update alpha state with latest book metrics and return price skew in dollars.
    #[inline(always)]
    pub fn update(&mut self, book: &LimitOrderBook) -> f64 {
        // Read recent OFI accumulator from the book
        let raw_ofi = book.ofi_accumulator as f64;

        // Exponential moving average smoothing
        self.smoothed_ofi = self.decay_factor * self.smoothed_ofi + (1.0 - self.decay_factor) * raw_ofi;

        // Linear price impact model: delta_P = multiplier * OFI
        let raw_skew = self.smoothed_ofi * self.alpha_multiplier;

        // Clip to safety boundary to avoid excessive distortion
        raw_skew.clamp(-self.max_skew_price, self.max_skew_price)
    }

    #[inline(always)]
    pub fn current_skew(&self) -> f64 {
        let raw_skew = self.smoothed_ofi * self.alpha_multiplier;
        raw_skew.clamp(-self.max_skew_price, self.max_skew_price)
    }

    #[inline(always)]
    pub fn reset(&mut self) {
        self.smoothed_ofi = 0.0;
    }
}
