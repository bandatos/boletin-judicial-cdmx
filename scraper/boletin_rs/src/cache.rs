// Cachés por hilo para funciones puras de un texto (lo que en Python lleva
// functools.lru_cache). Al llegar al máximo se vacían enteras.
use std::cell::RefCell;
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};
use std::thread::LocalKey;

/// FxHash (el de rustc): mucho más barato que SipHash para claves cortas.
#[derive(Default, Clone, Copy)]
pub struct Fx(u64);
const K: u64 = 0x51_7c_c1_b7_27_22_0a_95;
impl Fx {
    #[inline] fn add(&mut self, w: u64) { self.0 = (self.0.rotate_left(5) ^ w).wrapping_mul(K); }
}
impl Hasher for Fx {
    #[inline]
    fn write(&mut self, mut b: &[u8]) {
        while b.len() >= 8 { self.add(u64::from_le_bytes(b[..8].try_into().unwrap())); b = &b[8..]; }
        if b.len() >= 4 { self.add(u32::from_le_bytes(b[..4].try_into().unwrap()) as u64); b = &b[4..]; }
        for &x in b { self.add(x as u64); }
    }
    #[inline] fn write_u8(&mut self, i: u8) { self.add(i as u64); }
    #[inline] fn write_usize(&mut self, i: usize) { self.add(i as u64); }
    #[inline] fn finish(&self) -> u64 { self.0 }
}
pub type FxMap<K, V> = HashMap<K, V, BuildHasherDefault<Fx>>;

pub struct Cache<V> { m: FxMap<Box<str>, V>, max: usize }
impl<V> Cache<V> {
    pub fn new(max: usize) -> Cache<V> { Cache { m: FxMap::default(), max } }
}

/// f(k) con caché por hilo en `c`.
pub fn memo<V: Clone>(c: &'static LocalKey<RefCell<Cache<V>>>, k: &str, f: impl FnOnce() -> V) -> V {
    if let Some(v) = c.with(|c| c.borrow().m.get(k).cloned()) { return v; }
    let v = f();
    c.with(|c| {
        let mut c = c.borrow_mut();
        if c.m.len() >= c.max { c.m.clear(); }
        c.m.insert(k.into(), v.clone());
    });
    v
}
