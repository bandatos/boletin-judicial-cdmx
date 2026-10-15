// slug.js — genera el slug legible para las URLs de /expediente/:idslug.
// El id real (entradas.id de CUALQUIER aviso de ese expediente) sigue siendo
// la única fuente de verdad para el lookup; el slug es solo decorativo/SEO.

function slugify(text) {
  return String(text || '')
    .normalize('NFD').replace(/[̀-ͯ]/g, '') // quitar acentos
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 60);
}

function expedienteSlug(exp) {
  const parts = [exp.actora, exp.expediente].filter(Boolean).map(slugify).filter(Boolean);
  return parts.join('-') || 'expediente';
}

function expedienteHref(exp) {
  return `/expediente/${exp.id}-${expedienteSlug(exp)}`;
}

function juzgadoHref(nombreJuzgado) {
  return `/juzgado/${slugify(nombreJuzgado)}`;
}

module.exports = { slugify, expedienteSlug, expedienteHref, juzgadoHref };
