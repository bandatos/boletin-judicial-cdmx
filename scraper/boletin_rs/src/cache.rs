// Caché de funciones puras de un texto, compartida entre hilos.
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};
use std::sync::Mutex;

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

/// Caché compartida entre hilos para funciones puras de un texto (lo que en
/// Python lleva functools.lru_cache). Repartida en segmentos con su propio
/// candado para que los hilos casi nunca se esperen. Antes era una caché por
/// hilo: con 19 hilos cada uno veía una parte de los datos y recalculaba lo
/// que ya había calculado otro (el CPU total se triplicaba). Al llegar al
/// máximo, un segmento se vacía entero.
pub struct Memo<V> { segmentos: Vec<Mutex<FxMap<Box<str>, V>>>, max_por_segmento: usize }

const SEGMENTOS: usize = 128;

impl<V: Clone> Memo<V> {
    pub fn new(max: usize) -> Memo<V> {
        Memo { segmentos: (0..SEGMENTOS).map(|_| Mutex::new(FxMap::default())).collect(),
               max_por_segmento: max / SEGMENTOS + 1 }
    }

    /// f(k), calculado una sola vez mientras siga en la caché.
    pub fn get(&self, k: &str, f: impl FnOnce() -> V) -> V {
        let mut h = Fx::default();
        h.write(k.as_bytes());
        let seg = &self.segmentos[(h.finish() >> 7) as usize % SEGMENTOS];
        if let Some(v) = seg.lock().unwrap().get(k) { return v.clone(); }
        let v = f();
        let mut m = seg.lock().unwrap();
        if m.len() >= self.max_por_segmento { m.clear(); }
        m.insert(k.into(), v.clone());
        v
    }
}
