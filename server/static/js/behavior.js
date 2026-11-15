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
    document.getElementById('pdf-panel-title').textContent = titulo || 'Boletín';
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
    eventoGa(a.getAttribute('data-tipo') === 'sentencia' ? 'ver_sentencia' : 'ver_pdf');
  });
  document.getElementById('pdf-panel-close').addEventListener('click', cerrar);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && !panel.hidden) cerrar();
  });
})();

// Avisos tal como salieron en el boletín (ficha del expediente): al abrir
// el desplegable se pide el texto de cada uno, de a uno para no cargar el
// server (cada aviso lee el archivo de su boletín).
(function () {
  document.addEventListener('toggle', function (e) {
    var d = e.target;
    if (!d.classList || !d.classList.contains('avisos-textos') || !d.open || d.getAttribute('data-cargado')) return;
    d.setAttribute('data-cargado', '1');
    var items = d.querySelectorAll('li[data-aviso]');
    var i = 0;
    function siguiente() {
      if (i >= items.length) return;
      var li = items[i++];
      var cuerpo = li.querySelector('.aviso-texto-cuerpo');
      cuerpo.textContent = 'Cargando…';
      fetch('/api/aviso/' + li.getAttribute('data-aviso') + '?json=1')
        .then(function (r) { return r.json(); })
        .then(function (j) { cuerpo.textContent = j.texto || 'No encontramos el texto en el boletín.'; })
        .catch(function () { cuerpo.textContent = 'No se pudo cargar el texto.'; })
        .then(siguiente);
    }
    siguiente();
    eventoGa('ver_texto_aviso');
  }, true);
})();

// "Ver los N avisos" en las listas de expedientes: la línea de tiempo se
// pide al desplegar (/expediente/:id/avisos), la misma que en la ficha.
document.addEventListener('toggle', function (e) {
  var d = e.target;
  if (!d.classList || !d.classList.contains('exp-avisos') || !d.open || d.getAttribute('data-cargado')) return;
  d.setAttribute('data-cargado', '1');
  var lista = d.querySelector('.exp-avisos-lista');
  var enlace = lista.innerHTML;
  lista.textContent = 'Cargando…';
  fetch('/expediente/' + d.getAttribute('data-expediente') + '/avisos')
    .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
    .then(function (html) { lista.innerHTML = html; })
    .catch(function () { lista.innerHTML = enlace; d.removeAttribute('data-cargado'); });
  eventoGa('ver_avisos_lista');
}, true);

// Maqueta de avisos por correo: cuánta gente la pide y si escribió algo en
// el campo. Nunca se manda el correo (el campo no tiene name y acá solo se
// mira si está vacío).
document.addEventListener('submit', function (e) {
  var f = e.target;
  if (!f.closest || !f.closest('.alerta-maqueta')) return;
  var campo = f.querySelector('input[type="email"]');
  eventoGa('alerta_pedida', { con_correo: campo && campo.value.trim() ? 'si' : 'no' });
}, true);

// Interés en avisos por correo de una empresa (maqueta sin formulario).
document.addEventListener('toggle', function (e) {
  var d = e.target;
  if (d.classList && d.classList.contains('alerta-empresa') && d.open) eventoGa('alerta_pedida', { tipo: 'empresa' });
}, true);

// Acciones (botones y enlaces con data-accion): un evento "accion" con su
// nombre. tipo_pagina dice en qué página (eventoGa). Las que tienen evento
// propio (guardar, ver_pdf, ir_tdv, alerta_pedida) no llevan data-accion.
document.addEventListener('click', function (e) {
  var a = e.target.closest && e.target.closest('[data-accion]');
  if (a) eventoGa('accion', { accion: a.getAttribute('data-accion') });
}, true);
// Abrir el formulario de avisos por correo de la ficha (enviarlo es
// alerta_pedida).
document.addEventListener('toggle', function (e) {
  var d = e.target;
  if (d.classList && d.classList.contains('alerta-maqueta') && !d.classList.contains('alerta-empresa') && d.open) {
    eventoGa('accion', { accion: 'alerta_abrir' });
  }
}, true);

// Clics hacia Todo Derecho Vecino (orientación legal): evento propio, con la
// página desde la que se fue. La medición automática de clics salientes de
// GA puede no contarlo, porque está en el mismo dominio (bandatos.org).
document.addEventListener('click', function (e) {
  var a = e.target.closest && e.target.closest('a[href*="todo-derecho-vecino"]');
  if (a) eventoGa('ir_tdv');
});

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
    if (e.target.matches && e.target.matches('form.opinion')) return; // tiene su propio estado
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
    if (texto === 'url') texto = location.origin + location.pathname;
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
    // Mismo componente que las listas del sitio (mixins.pug: expedienteItem):
    // número y órgano, partes, tipo con el último aviso y los avisos al
    // desplegar. Arriba, el nombre que le pone la persona y si hay novedades.
    filas.forEach(function (f) {
      var g = f.guardado, d = f.dato || {};
      var li = el('li', 'mis-item');
      var art = el('article', 'exp-item');
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
      art.appendChild(cab);

      var header = el('header');
      var num = el(d.encontrado ? 'a' : 'span', 'exp-item-num', 'Exp. ' + (d.expediente || g.expediente || '—'));
      if (d.encontrado) num.href = d.href;
      header.appendChild(num);
      // Orden fijo: expediente, tipo de juicio, juzgado, secretaría.
      if (d.tipo) header.appendChild(el('span', 'exp-item-tipo', d.tipo));
      header.appendChild(el('span', 'exp-item-organo', [d.organo || g.organo, d.secretaria].filter(Boolean).join(' · ')));
      var u = d.ultimo_aviso || {};
      if (d.sentencia) {
        var marca = el('span', 'marca-sentencia');
        var et = el('span', 'actuacion act-sentencia', 'Sentencia ' + fecha(d.sentencia).replace('.', ''));
        et.title = 'Sentencia publicada en el boletín del ' + fecha(d.sentencia);
        marca.appendChild(et);
        header.appendChild(marca);
      }
      art.appendChild(header);

      var partes = el('div', 'exp-item-partes');
      if (d.encontrado && d.demandada) {
        // Misma etiqueta "vs" que en el resto del sitio (mixins.pug: vs).
        partes.appendChild(el('b', null, d.actora));
        partes.appendChild(document.createTextNode(' '));
        var vs = el('span', 'vs', 'vs.');
        vs.setAttribute('aria-label', 'contra');
        partes.appendChild(vs);
        partes.appendChild(document.createTextNode(' '));
        partes.appendChild(el('b', null, d.demandada));
      } else {
        partes.textContent = d.encontrado ? d.titulo : (g.titulo || 'Expediente') + ' (ya no está en la base)';
      }
      art.appendChild(partes);

      if (d.ultima_fecha) {
        var meta = el('p', 'exp-item-meta');
        meta.appendChild(el('span', null, d.avisos > 1 ? d.avisos + ' avisos · último:' : 'Aviso:'));
        meta.appendChild(el('span', null, fecha(d.ultima_fecha)));
        if (u.etiqueta) meta.appendChild(el('span', 'actuacion ' + (u.clase || ''), u.etiqueta));
        if (u.descripcion && u.descripcion !== u.etiqueta) meta.appendChild(el('span', 'lt-desc', u.descripcion));
        if (u.extra) meta.appendChild(el('span', 'lt-extra', u.extra));
        art.appendChild(meta);
      }
      if (d.encontrado && d.avisos > 1) {
        var det = el('details', 'exp-avisos');
        det.setAttribute('data-expediente', g.id);
        det.appendChild(el('summary', null, 'Ver los ' + d.avisos + ' avisos'));
        var lista = el('div', 'exp-avisos-lista');
        var enFicha = el('a', null, 'Ver los avisos en la ficha del expediente');
        enFicha.href = d.href;
        lista.appendChild(enFicha);
        det.appendChild(lista);
        art.appendChild(det);
      }
      var quitar = el('button', 'boton-borde mis-quitar', 'Quitar');
      quitar.type = 'button';
      quitar.addEventListener('click', function () {
        escribir((leer() || []).filter(function (x) { return x.id !== g.id; }));
        li.remove();
      });
      art.appendChild(quitar);
      li.appendChild(art);
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

  document.addEventListener('click', function (e) {
    if (!e.target.closest) return;
    var opcion = caja && e.target.closest('#intencion button[data-valor]');
    if (opcion) {
      guardar('boletin-intencion', opcion.getAttribute('data-valor'));
      guardar('boletin-intencion-fecha', String(Date.now()));
      eventoGa('intencion', { valor: opcion.getAttribute('data-valor') });
      caja.querySelector('.intencion-pregunta').hidden = true;
      caja.querySelector('.intencion-gracias').hidden = false;
      caja.querySelector('textarea').focus();
      return;
    }
    var cerrar = e.target.closest('.intencion-cerrar');
    if (cerrar) {
      var franja = cerrar.closest('.intencion');
      // Cerrar la pregunta sin responder la marca como cerrada. Cerrar el
      // formulario sin enviarlo (después de responder o en la reoferta) es
      // un "no"; después de enviarlo, solo se cierra.
      if (franja === caja && !caja.querySelector('.intencion-pregunta').hidden) {
        guardar('boletin-intencion', 'cerrada');
        eventoGa('intencion_cerrada');
      } else if (leer('boletin-entrevista') !== 'si') {
        guardar('boletin-entrevista', 'no');
        eventoGa('entrevista_rechazada', { lugar: franja === caja ? 'pregunta' : 'reoferta' });
      }
      franja.hidden = true;
      return;
    }
    var abrir = e.target.closest('.opinion-abrir');
    if (abrir) {
      var destino = document.getElementById(abrir.getAttribute('aria-controls'));
      destino.hidden = !destino.hidden;
      abrir.setAttribute('aria-expanded', String(!destino.hidden));
      if (!destino.hidden) destino.querySelector('textarea').focus();
      return;
    }
    var no = e.target.closest('.entrevista-no');
    if (no) {
      guardar('boletin-entrevista', 'no');
      eventoGa('entrevista_rechazada', { lugar: no.closest('form').getAttribute('data-lugar') });
      no.closest('.intencion').hidden = true;
    }
  });

  // Formulario de opinión (form-opinion.pug): se envía sin salir de la
  // página. Sin fetch, el navegador lo manda a /opinion como cualquier form.
  var empezados = {};
  document.addEventListener('focusin', function (e) {
    var form = e.target.closest && e.target.closest('form.opinion');
    if (!form || empezados[form.getAttribute('data-lugar')]) return;
    empezados[form.getAttribute('data-lugar')] = true;
    eventoGa('opinion_empezada', { lugar: form.getAttribute('data-lugar') });
  });

  document.addEventListener('submit', function (e) {
    var form = e.target;
    if (!form.matches || !form.matches('form.opinion') || typeof fetch !== 'function') return;
    e.preventDefault();
    var lugar = form.getAttribute('data-lugar');
    var error = form.querySelector('.opinion-error');
    var mostrarError = function (texto) { error.textContent = texto; error.hidden = false; };
    var valor = function (nombre) { return form.elements[nombre].value.trim(); };
    var conContacto = !!(valor('nombre') || valor('contacto'));
    if (!valor('bueno') && !valor('falta')) return mostrarError('Escribe algo en al menos uno de los dos campos.');
    if (conContacto && !form.elements.acepto.checked) return mostrarError('Para dejarnos tus datos, marca que aceptas que te contactemos.');
    var intencion = leer('boletin-intencion');
    if (intencion && intencion !== 'cerrada') form.elements.intencion.value = intencion;
    var boton = form.querySelector('button[type=submit]');
    boton.disabled = true;
    boton.textContent = 'Enviando…';
    error.hidden = true;
    fetch(form.action, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(new FormData(form)).toString()
    }).then(function (r) { return r.json(); }).then(function (r) {
      if (!r.ok) { var x = new Error(r.mensaje); x.delServer = true; throw x; }
      guardar('boletin-entrevista', 'si');
      eventoGa('opinion_enviada', { lugar: lugar, con_contacto: conContacto ? 'si' : 'no' });
      form.hidden = true;
      var gracias = form.nextElementSibling;
      gracias.hidden = false;
      var franja = form.closest('.intencion');
      if (franja) {
        Array.prototype.forEach.call(franja.querySelectorAll('h2, .nota'), function (n) { if (!form.contains(n)) n.hidden = true; });
      }
    }).catch(function (err) {
      eventoGa('opinion_error', { lugar: lugar });
      mostrarError(err.delServer && err.message ? err.message : 'No se pudo enviar. Intenta de nuevo en unos minutos.');
      boton.disabled = false;
      boton.textContent = 'Enviar';
    });
  });
})();
