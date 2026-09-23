use std::fmt;
use std::ops::{Add, AddAssign, Sub, SubAssign};

/// Fixed-point scalar for Price and Qty arithmetic (10^8 multiplier, i.e., 8 decimals).
pub const PRICE_SCALE: i64 = 100_000_000;
pub const QTY_SCALE: u64 = 100_000_000;

/// Fixed-point Price representation to prevent floating-point imprecision and jitter.
#[derive(Copy, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
#[repr(transparent)]
pub struct Price(pub i64);

impl Price {
    pub const ZERO: Price = Price(0);
    pub const MAX: Price = Price(i64::MAX);
    pub const MIN: Price = Price(i64::MIN);

    #[inline(always)]
    pub const fn from_raw(raw: i64) -> Self {
        Price(raw)
    }

    #[inline(always)]
    pub fn from_f64(val: f64) -> Self {
        Price((val * PRICE_SCALE as f64).round() as i64)
    }

    #[inline(always)]
    pub fn to_f64(self) -> f64 {
        self.0 as f64 / PRICE_SCALE as f64
    }

    #[inline(always)]
    pub const fn raw(self) -> i64 {
        self.0
    }
}

impl fmt::Debug for Price {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:.4}", self.to_f64())
    }
}

impl fmt::Display for Price {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:.4}", self.to_f64())
    }
}

impl Add for Price {
    type Output = Self;
    #[inline(always)]
    fn add(self, rhs: Self) -> Self::Output {
        Price(self.0 + rhs.0)
    }
}

impl AddAssign for Price {
    #[inline(always)]
    fn add_assign(&mut self, rhs: Self) {
        self.0 += rhs.0;
    }
}

impl Sub for Price {
    type Output = Self;
    #[inline(always)]
    fn sub(self, rhs: Self) -> Self::Output {
        Price(self.0 - rhs.0)
    }
}

impl SubAssign for Price {
    #[inline(always)]
    fn sub_assign(&mut self, rhs: Self) {
        self.0 -= rhs.0;
    }
}

/// Fixed-point Quantity representation.
#[derive(Copy, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
#[repr(transparent)]
pub struct Qty(pub u64);

impl Qty {
    pub const ZERO: Qty = Qty(0);

    #[inline(always)]
    pub const fn from_raw(raw: u64) -> Self {
        Qty(raw)
    }

    #[inline(always)]
    pub fn from_f64(val: f64) -> Self {
        Qty((val * QTY_SCALE as f64).round() as u64)
    }

    #[inline(always)]
    pub fn to_f64(self) -> f64 {
        self.0 as f64 / QTY_SCALE as f64
    }

    #[inline(always)]
    pub const fn raw(self) -> u64 {
        self.0
    }

    #[inline(always)]
    pub const fn is_zero(self) -> bool {
        self.0 == 0
    }
}

impl fmt::Debug for Qty {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:.4}", self.to_f64())
    }
}

impl fmt::Display for Qty {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:.4}", self.to_f64())
    }
}

impl Add for Qty {
    type Output = Self;
    #[inline(always)]
    fn add(self, rhs: Self) -> Self::Output {
        Qty(self.0 + rhs.0)
    }
}

impl AddAssign for Qty {
    #[inline(always)]
    fn add_assign(&mut self, rhs: Self) {
        self.0 += rhs.0;
    }
}

impl Sub for Qty {
    type Output = Self;
    #[inline(always)]
    fn sub(self, rhs: Self) -> Self::Output {
        Qty(self.0.saturating_sub(rhs.0))
    }
}

impl SubAssign for Qty {
    #[inline(always)]
    fn sub_assign(&mut self, rhs: Self) {
        self.0 = self.0.saturating_sub(rhs.0);
    }
}

/// Unique Order Identifier.
pub type OrderId = u64;

/// Order Book Side.
#[derive(Copy, Clone, Debug, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum Side {
    Bid = 0,
    Ask = 1,
}

impl Side {
    #[inline(always)]
    pub fn opposite(self) -> Self {
        match self {
            Side::Bid => Side::Ask,
            Side::Ask => Side::Bid,
        }
    }

    #[inline(always)]
    pub fn is_bid(self) -> bool {
        matches!(self, Side::Bid)
    }

    #[inline(always)]
    pub fn is_ask(self) -> bool {
        matches!(self, Side::Ask)
    }
}

impl fmt::Display for Side {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Side::Bid => write!(f, "BID"),
            Side::Ask => write!(f, "ASK"),
        }
    }
}

/// High-resolution Nanoseconds timestamp.
#[derive(Copy, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Default)]
#[repr(transparent)]
pub struct Nanoseconds(pub u64);

impl Nanoseconds {
    pub const ZERO: Nanoseconds = Nanoseconds(0);

    #[inline(always)]
    pub const fn from_raw(raw: u64) -> Self {
        Nanoseconds(raw)
    }

    #[inline(always)]
    pub fn as_nanos(self) -> u64 {
        self.0
    }

    #[inline(always)]
    pub fn as_micros(self) -> f64 {
        self.0 as f64 / 1_000.0
    }

    #[inline(always)]
    pub fn as_millis(self) -> f64 {
        self.0 as f64 / 1_000_000.0
    }
}

impl fmt::Debug for Nanoseconds {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}ns ({:.2}µs)", self.0, self.as_micros())
    }
}

impl fmt::Display for Nanoseconds {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:.2}µs", self.as_micros())
    }
}
