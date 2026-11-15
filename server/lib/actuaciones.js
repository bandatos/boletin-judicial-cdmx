// actuaciones.js — etiqueta de cada aviso (qué pasó) y su clase de color.
// La usan las plantillas (views.js) y las listas que arma db.js.

// Etiquetas de cada aviso. Cada una tiene su clase de color en main.css
// (.act-*): la sentencia definitiva es la más visible (negro sólido), la
// interlocutoria va con borde, la prevención en ámbar (pide hacer algo), la
// negación en rojo, y admisión, audiencia y acuerdo en neutro. El verde
// queda reservado para links y botones.
const ACTUACIONES = {
  sentencia_definitiva: 'Sentencia definitiva',
  sentencia_interlocutoria: 'Sentencia interlocutoria',
  // Las de sala no dicen si son definitivas: casi siempre resuelven la
  // apelación, por eso van con el mismo peso que la definitiva.
  sentencia: 'Sentencia',
  audiencia: 'Audiencia',
};
const TIPOS_ACUERDO = {
  prevencion: 'Prevención',
  negacion: 'Negación',
  admision: 'Admisión',
};
function esSentenciaFinal(a) {
  return a.tipo_actuacion === 'sentencia_definitiva' || a.tipo_actuacion === 'sentencia';
}
function actuacion(a) {
  if (a.tipo_actuacion && ACTUACIONES[a.tipo_actuacion]) return ACTUACIONES[a.tipo_actuacion];
  if (a.tipo_acuerdo && TIPOS_ACUERDO[a.tipo_acuerdo]) return TIPOS_ACUERDO[a.tipo_acuerdo];
  if (a.num_acdos) return `${a.num_acdos} acuerdo${a.num_acdos !== 1 ? 's' : ''}`;
  return a.tipo_actuacion === 'acuerdo' ? 'Acuerdo' : null;
}
function actuacionClase(a) {
  if (esSentenciaFinal(a)) return 'act-sentencia';
  if (a.tipo_actuacion === 'sentencia_interlocutoria') return 'act-interlocutoria';
  if (a.tipo_actuacion === 'audiencia') return 'act-audiencia';
  if (a.tipo_acuerdo && TIPOS_ACUERDO[a.tipo_acuerdo]) return `act-${a.tipo_acuerdo}`;
  return 'act-acuerdo';
}
// La etiqueta es solo el conteo ("4 acuerdos", "Acuerdo"): no hay un tipo.
function esConteo(a) {
  return !(a.tipo_actuacion && ACTUACIONES[a.tipo_actuacion]) && !(a.tipo_acuerdo && TIPOS_ACUERDO[a.tipo_acuerdo]);
}
// Qué mostrar como etiqueta. Si el aviso es solo un conteo y el juzgado
// escribió una frase, la frase va primero y el conteo pasa a acuerdosExtra:
// el boletín da cuántos acuerdos hubo ese día y una sola frase, no un texto
// por acuerdo ("Remite resultado diligencia · 4 acuerdos ese día").
function etiqueta(a) {
  if (esConteo(a) && a.descripcion) return null;
  return actuacion(a);
}
function acuerdosExtra(a) {
  if (!a.num_acdos || a.num_acdos < 2) return null;
  if (esConteo(a) && !a.descripcion) return null; // ya es la etiqueta
  return `${a.num_acdos} acuerdos ese día`;
}

// Abreviaturas frecuentes en lo que escriben los juzgados, expandidas en el
// texto para quien no es abogado ("Dom. Procesal Cóny. Sra." -> "Domicilio
// Procesal Cónyuge Señora"). Solo las de significado seguro; las demás
// quedan como están. Las mayúsculas se dejan como en el boletín, que pone
// mayúscula en cada palabra. El texto original sigue en "Ver los avisos tal
// como salieron en el boletín". "Rpp" (¿Registro Público de la Propiedad?)
// no está: falta verificarlo.
const ABREVIATURAS = {
  'Acdo.': 'Acuerdo', 'Acdos.': 'Acuerdos', 'Notif.': 'Notificación', 'Exh.': 'Exhorto',
  'Dom.': 'Domicilio', 'Apel.': 'Apelación', 'Amp.': 'Amparo', 'Inc.': 'Incidente',
  'Aud.': 'Audiencia', 'Sent.': 'Sentencia', 'Cump.': 'Cumplimiento', 'Reb.': 'Rebeldía',
  'Of.': 'Oficio', 'Cóny.': 'Cónyuge', 'Cony.': 'Cónyuge', 'Expdllo.': 'Expedientillo',
  'Sr.': 'Señor', 'Sra.': 'Señora', 'Sr': 'Señor', 'Sra': 'Señora', 'Mp': 'Ministerio Público',
};
const RE_ABREV = new RegExp(
  `(^|[\\s(,;])(${Object.keys(ABREVIATURAS).sort((a, b) => b.length - a.length).map(k => k.replace('.', '\\.')).join('|')})(?=$|[\\s),;])`, 'g');
function expandirAbreviaturas(texto) {
  if (!texto) return texto;
  return texto.replace(RE_ABREV, (m, pre, ab) => pre + ABREVIATURAS[ab]);
}

// "SECRETARÍA “A”" -> "Secretaría A".
function secretariaCorta(s) {
  if (!s) return null;
  const letra = s.replace(/^SECRETAR[IÍ]A\s*/i, '').replace(/[“”"]/g, '').trim();
  return letra ? `Secretaría ${letra}` : null;
}

module.exports = {
  secretariaCorta,
  ACTUACIONES, TIPOS_ACUERDO, actuacion, actuacionClase, acuerdosExtra, esSentenciaFinal, etiqueta, expandirAbreviaturas,
};
