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
