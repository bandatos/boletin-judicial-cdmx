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
