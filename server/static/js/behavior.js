// Evento de Google Analytics, si está cargado (en desarrollo, en los tests o
// con bloqueadores no hace nada). tipo_pagina: primera parte de la ruta.
function eventoGa(nombre, params) {
  if (typeof window.gtag !== 'function') return;
  var p = params || {};
  p.tipo_pagina = location.pathname.split('/')[1] || 'portada';
  window.gtag('event', nombre, p);
}

// behavior.js — panel lateral con el PDF. La búsqueda es 100% server-side
// (formulario GET, sin fetch/XHR).

// Panel lateral con el PDF del boletín, para comparar lo parseado contra el
// original. Los links .ver-pdf lo abren en la página del aviso (#page=N);
// con Ctrl o clic del medio siguen siendo links comunes.
(function () {
  var panel = document.getElementById('pdf-panel');
  if (!panel) return;

  function abrir(url, pagina, titulo) {
    var href = pagina ? url + '#page=' + pagina : url;
    var actual = document.getElementById('pdf-frame');
    // Si solo cambia el #page, el visor de Chrome no salta de página (ni
    // cambiando el src): se reemplaza el iframe. El PDF sale de la caché.
    if (actual.getAttribute('data-href') !== href) {
      var frame = document.createElement('iframe');
      frame.id = 'pdf-frame';
      frame.title = actual.title;
      frame.setAttribute('data-href', href);
      frame.src = href;
      actual.parentNode.replaceChild(frame, actual);
    }
    document.getElementById('pdf-panel-title').textContent = titulo || 'PDF del boletín';
    document.getElementById('pdf-panel-open').href = href;
    panel.hidden = false;
    document.body.classList.add('pdf-open');
  }

  function cerrar() {
    panel.hidden = true;
    document.body.classList.remove('pdf-open');
    var marcados = document.querySelectorAll('.comparando');
    for (var i = 0; i < marcados.length; i++) marcados[i].classList.remove('comparando');
  }

  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a.ver-pdf');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    var marcados = document.querySelectorAll('.comparando');
    for (var i = 0; i < marcados.length; i++) marcados[i].classList.remove('comparando');
    var item = a.closest('li, .card');
    if (item) item.classList.add('comparando');
    abrir(a.getAttribute('data-pdf'), a.getAttribute('data-pagina'), a.getAttribute('data-titulo'));
    eventoGa('ver_pdf');
  });
  document.getElementById('pdf-panel-close').addEventListener('click', cerrar);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !panel.hidden) cerrar();
  });
})();

// Pestañas: cada botón muestra su tabpanel y oculta los otros del mismo
// grupo. Teclado como el patrón ARIA de pestañas: flechas, Inicio y Fin
// mueven entre pestañas; solo la activa está en el orden de tabulación.
(function () {
  function delGrupo(tab) {
    return document.querySelectorAll('button.pestana[data-grupo="' + tab.getAttribute('data-grupo') + '"]');
  }
  function activar(tab, enfocar) {
    var tabs = delGrupo(tab);
    for (var i = 0; i < tabs.length; i++) {
      var activa = tabs[i] === tab;
      tabs[i].setAttribute('aria-selected', activa ? 'true' : 'false');
      tabs[i].tabIndex = activa ? 0 : -1;
      document.getElementById(tabs[i].getAttribute('aria-controls')).hidden = !activa;
    }
    if (enfocar) tab.focus();
  }
  var iniciales = document.querySelectorAll('button.pestana');
  for (var i = 0; i < iniciales.length; i++) {
    iniciales[i].tabIndex = iniciales[i].getAttribute('aria-selected') === 'true' ? 0 : -1;
  }
  document.addEventListener('click', function (e) {
    var tab = e.target.closest && e.target.closest('button.pestana');
    if (tab) activar(tab, false);
  });
  document.addEventListener('keydown', function (e) {
    var tab = e.target.closest && e.target.closest('button.pestana');
    if (!tab) return;
    var tabs = Array.prototype.slice.call(delGrupo(tab));
    var k = tabs.indexOf(tab);
    var destino = { ArrowRight: k + 1, ArrowLeft: k - 1, Home: 0, End: tabs.length - 1 }[e.key];
    if (destino === undefined) return;
    e.preventDefault();
    activar(tabs[(destino + tabs.length) % tabs.length], true);
  });
})();

// Términos de búsqueda en Google Analytics: al enviar cualquier caja de
// búsqueda (name="q") con texto se manda el evento estándar "search" con lo
// escrito y la sección, y después sigue el envío. Si GA no confirma en
// 800 ms, se envía igual. Sin GA (desarrollo, bloqueadores) no hace nada.
(function () {
  document.addEventListener('submit', function (e) {
    var form = e.target;
    var campo = form.querySelector('input[name="q"]');
    if (!campo || typeof window.gtag !== 'function' || form.getAttribute('data-registrada')) return;
    var termino = campo.value.trim();
    if (!termino) return;
    e.preventDefault();
    form.setAttribute('data-registrada', '1');
    var enviado = false;
    function seguir() {
      if (enviado) return;
      enviado = true;
      form.submit();
    }
    window.gtag('event', 'search', {
      search_term: termino,
      seccion: location.pathname.split('/')[1] || 'portada',
      transport_type: 'beacon',
      event_callback: seguir,
    });
    setTimeout(seguir, 800);
  });
  // Al volver con "atrás", que la próxima búsqueda se registre de nuevo.
  window.addEventListener('pageshow', function () {
    var forms = document.querySelectorAll('form[data-registrada]');
    for (var i = 0; i < forms.length; i++) forms[i].removeAttribute('data-registrada');
  });
})();

// Formularios de búsqueda: el botón avisa que está buscando (algunas
// búsquedas tardan uno o dos segundos en el server).
(function () {
  document.addEventListener('submit', function (e) {
    var boton = e.target.querySelector('button[type="submit"]');
    if (!boton || boton.getAttribute('aria-busy') === 'true') return;
    boton.setAttribute('aria-busy', 'true');
    boton.setAttribute('data-texto', boton.textContent);
    boton.textContent = 'Buscando…';
  });
  // Al volver con "atrás", el navegador puede mostrar la página guardada con
  // el botón en "Buscando…".
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    var botones = document.querySelectorAll('button[aria-busy="true"]');
    for (var i = 0; i < botones.length; i++) {
      botones[i].removeAttribute('aria-busy');
      botones[i].textContent = botones[i].getAttribute('data-texto') || 'Buscar';
    }
  });
})();

// Copiar el número de expediente (ficha).
(function () {
  document.addEventListener('click', function (e) {
    var b = e.target.closest && e.target.closest('button.boton-copiar');
    if (!b) return;
    var texto = b.getAttribute('data-copiar');
    var listo = function () {
      var antes = b.textContent;
      b.textContent = 'Copiado';
      setTimeout(function () { b.textContent = antes; }, 2000);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(texto).then(listo, function () {});
    }
  });
})();

// Mis expedientes: la lista se guarda solo en este navegador (localStorage),
// sin cuentas ni datos en el servidor. Cada elemento: { id, apodo, titulo,
// expediente, organo, visto } (visto: fecha del último aviso que la persona
// ya vio, para marcar lo nuevo).
(function () {
  var CLAVE = 'mis-expedientes';
  var MESES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];

  function leer() {
    try { return JSON.parse(localStorage.getItem(CLAVE)) || []; } catch (err) { return null; }
  }
  function escribir(lista) {
    try { localStorage.setItem(CLAVE, JSON.stringify(lista)); return true; } catch (err) { return false; }
  }
  function fecha(iso) {
    if (!iso) return '';
    var p = iso.split('-');
    return Number(p[2]) + ' ' + MESES[Number(p[1]) - 1] + '. ' + p[0];
  }
  function el(tag, clase, texto) {
    var n = document.createElement(tag);
    if (clase) n.className = clase;
    if (texto != null) n.textContent = texto;
    return n;
  }

  // Ficha: botón para guardar o quitar, y marcar como visto lo publicado.
  var boton = document.querySelector('button.boton-guardar');
  if (boton && leer() !== null) {
    var id = Number(boton.getAttribute('data-id'));
    var ultima = boton.getAttribute('data-ultima');
    var pintar = function () {
      var guardado = (leer() || []).some(function (x) { return x.id === id; });
      boton.textContent = guardado ? 'Guardado en Mis expedientes · Quitar' : 'Guardar en Mis expedientes';
      boton.setAttribute('aria-pressed', guardado ? 'true' : 'false');
    };
    var lista = leer();
    var item = lista.filter(function (x) { return x.id === id; })[0];
    if (item && ultima && (!item.visto || item.visto < ultima)) {
      item.visto = ultima;
      escribir(lista);
    }
    boton.hidden = false;
    pintar();
    boton.addEventListener('click', function () {
      var l = leer() || [];
      var ya = l.some(function (x) { return x.id === id; });
      if (ya) l = l.filter(function (x) { return x.id !== id; });
      else {
        l.push({
          id: id, apodo: '', titulo: boton.getAttribute('data-titulo'),
          expediente: boton.getAttribute('data-expediente'), organo: boton.getAttribute('data-organo'),
          visto: ultima,
        });
      }
      escribir(l);
      pintar();
      eventoGa(ya ? 'quitar_expediente' : 'guardar_expediente', { cantidad: l.length });
    });
  }

  // Página /mis-expedientes.
  var cont = document.getElementById('mis-expedientes');
  if (!cont) return;
  var lista = leer();
  cont.textContent = '';
  if (lista === null) {
    cont.appendChild(el('p', null, 'Tu navegador no permite guardar datos de este sitio, así que no se puede armar la lista.'));
    return;
  }
  if (!lista.length) {
    var vacio = el('div', 'mis-vacio');
    vacio.appendChild(el('p', null, 'Todavía no guardaste ningún expediente.'));
    var pasos = el('p', 'nota');
    pasos.appendChild(document.createTextNode('Busca por nombre en el '));
    var a = el('a', null, 'buscador');
    a.href = '/buscar';
    pasos.appendChild(a);
    pasos.appendChild(document.createTextNode(', abre el expediente y usa el botón «Guardar en Mis expedientes».'));
    vacio.appendChild(pasos);
    cont.appendChild(vacio);
    return;
  }
  eventoGa('mis_expedientes_con_lista', { cantidad: lista.length });
  cont.appendChild(el('p', 'nota', 'Cargando…'));
  var max = Number(cont.getAttribute('data-max')) || 100;
  var ids = lista.slice(0, max).map(function (x) { return x.id; }).join(',');
  fetch('/api/expedientes?ids=' + ids).then(function (r) { return r.json(); }).then(function (datos) {
    var porId = {};
    datos.expedientes.forEach(function (x) { porId[x.id] = x; });
    var filas = lista.map(function (x) { return { guardado: x, dato: porId[x.id] }; });
    filas.sort(function (a, b) {
      return ((b.dato && b.dato.ultima_fecha) || '').localeCompare((a.dato && a.dato.ultima_fecha) || '');
    });
    cont.textContent = '';
    if (datos.hasta) cont.appendChild(el('p', 'nota', 'Revisamos hasta el boletín del ' + fecha(datos.hasta) + '.'));
    var ol = el('ol', 'mis-lista');
    filas.forEach(function (f) {
      var g = f.guardado, d = f.dato || {};
      var li = el('li', 'card');
      var apodoId = 'apodo-' + g.id;
      var cab = el('div', 'mis-cabecera');
      var etiqueta = el('label', 'visualmente-oculto', 'Nombre para reconocer este expediente');
      etiqueta.htmlFor = apodoId;
      var apodo = el('input', 'mis-apodo');
      apodo.id = apodoId;
      apodo.type = 'text';
      apodo.maxLength = 80;
      apodo.placeholder = 'Ponle un nombre (opcional)';
      apodo.value = g.apodo || '';
      apodo.addEventListener('change', function () {
        var l = leer() || [];
        l.forEach(function (x) { if (x.id === g.id) x.apodo = apodo.value.trim(); });
        escribir(l);
      });
      cab.appendChild(etiqueta);
      cab.appendChild(apodo);
      if (d.ultima_fecha && (!g.visto || d.ultima_fecha > g.visto)) cab.appendChild(el('span', 'mis-nuevo', 'Nuevo aviso'));
      li.appendChild(cab);
      var titulo = el('div', 'card-partes');
      if (d.encontrado) {
        var link = el('a', null, d.titulo);
        link.href = d.href;
        titulo.appendChild(link);
      } else {
        titulo.textContent = (g.titulo || 'Expediente') + ' (ya no está en la base)';
      }
      li.appendChild(titulo);
      var meta = el('div', 'card-meta');
      [d.organo || g.organo, 'Exp. ' + (d.expediente || g.expediente), d.tipo].forEach(function (t) {
        if (t) meta.appendChild(el('span', null, t));
      });
      if (d.ultima_fecha) meta.appendChild(el('span', null, 'Último aviso: ' + fecha(d.ultima_fecha) + ' · ' + d.avisos + ' avisos'));
      li.appendChild(meta);
      var quitar = el('button', 'boton-borde mis-quitar', 'Quitar');
      quitar.type = 'button';
      quitar.addEventListener('click', function () {
        escribir((leer() || []).filter(function (x) { return x.id !== g.id; }));
        li.remove();
      });
      li.appendChild(quitar);
      ol.appendChild(li);
    });
    cont.appendChild(ol);
  }).catch(function () {
    cont.textContent = 'No se pudo cargar la lista. Prueba de nuevo en un rato.';
  });
})();

// Pregunta de intención, una vez por navegador. La respuesta va a GA
// (evento "intencion") y no se guarda en el server. Después se invita a una
// entrevista con un formulario de Google, con la respuesta ya marcada.
// Qué franja se ve lo decide head.pug antes de pintar (clases en <html>):
// acá solo se registran los eventos y se guardan las respuestas.
// Sin variables que dependan de otras del mismo bloque: el minificador de
// producción junta los var y los reordena.
(function () {
  var raiz = document.documentElement;
  var guardar = function (clave, valor) { try { localStorage.setItem(clave, valor); } catch (err) { /* sin almacenamiento */ } };
  var leer = function (clave) { try { return localStorage.getItem(clave); } catch (err) { return null; } };
  var caja = document.getElementById('intencion');
  var reoferta = document.getElementById('reoferta');
  if (caja && raiz.classList.contains('js') && !raiz.classList.contains('intencion-vista')) eventoGa('intencion_mostrada');
  if (reoferta && raiz.classList.contains('entrevista-reofrecer')) {
    guardar('boletin-reofrecida', String(Date.now()));
    eventoGa('intencion_reofrecida');
  }

  // Link al formulario con la respuesta a la pregunta ya marcada: opciones
  // de la primera pregunta del formulario y su id de campo.
  var OPCIONES_FORM = {
    propio: 'Revisar un juicio propio',
    conocido: 'Revisar el juicio de alguien que conozco',
    empresa: 'Averiguar sobre una empresa o un inmueble',
    trabajo: 'Por trabajo (casos de clientes, periodismo, investigación)'
  };
  var conRespuesta = function (link) {
    var texto = OPCIONES_FORM[leer('boletin-intencion')];
    if (texto && link.href.indexOf('entry.') < 0) link.href += '&entry.1051725648=' + encodeURIComponent(texto);
  };

  document.addEventListener('click', function (e) {
    if (!e.target.closest) return;
    var opcion = caja && e.target.closest('#intencion button[data-valor]');
    if (opcion) {
      guardar('boletin-intencion', opcion.getAttribute('data-valor'));
      guardar('boletin-intencion-fecha', String(Date.now()));
      eventoGa('intencion', { valor: opcion.getAttribute('data-valor') });
      caja.querySelector('.entrevista-texto').textContent = opcion.getAttribute('data-invita');
      caja.querySelector('.intencion-pregunta').hidden = true;
      caja.querySelector('.intencion-gracias').hidden = false;
      caja.querySelector('.entrevista-si').focus();
      return;
    }
    var cerrar = e.target.closest('.intencion-cerrar');
    if (cerrar) {
      var franja = cerrar.closest('.intencion');
      // Cerrar la pregunta sin responder la marca como cerrada. Cerrar la
      // invitación (después de responder o en la reoferta) es un "no".
      if (franja === caja && !caja.querySelector('.intencion-pregunta').hidden) {
        guardar('boletin-intencion', 'cerrada');
        eventoGa('intencion_cerrada');
      } else {
        guardar('boletin-entrevista', 'no');
        eventoGa('entrevista_rechazada', { lugar: franja === caja ? 'pregunta' : 'reoferta' });
      }
      franja.hidden = true;
      return;
    }
    var si = e.target.closest('.entrevista-si');
    if (si) {
      conRespuesta(si);
      guardar('boletin-entrevista', 'si');
      eventoGa('entrevista_click', { lugar: si.closest('.entrevista').getAttribute('data-lugar') });
      return;
    }
    var no = e.target.closest('.entrevista-no');
    if (no) {
      guardar('boletin-entrevista', 'no');
      eventoGa('entrevista_rechazada', { lugar: no.closest('.entrevista').getAttribute('data-lugar') });
      no.closest('.intencion').hidden = true;
    }
  });
})();
