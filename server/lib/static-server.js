// static-server.js — sirve /static/* con minificación al vuelo (igual que
// sendStatic() en sociedad-web-front/lib/router.js): si el .min es más
// viejo que el original, se reminifica y se cachea a disco.

const fs = require('fs');
const path = require('path');
const { minify } = require('minify');

const STATIC_DIR = path.join(__dirname, '..', 'static');

const MIME = {
  '.css': 'text/css; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.ico': 'image/x-icon',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.webp': 'image/webp',
  '.json': 'application/json; charset=utf-8',
};

// versionado: la URL trae ?v=<hash del contenido> (views.asset): se cachea un
// año como inmutable; si cambia el archivo cambia la URL.
async function serveStatic(file, res, versionado = false) {
  const safePath = path.normalize(file).replace(/^(\.\.[/\\])+/, '');
  const fullPath = path.join(STATIC_DIR, safePath);

  if (!fullPath.startsWith(STATIC_DIR) || !fs.existsSync(fullPath) || fs.statSync(fullPath).isDirectory()) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Not found');
    return;
  }

  const ext = path.extname(fullPath);
  let content;

  const minifiable = (ext === '.css' || ext === '.js') && !fullPath.endsWith(`.min${ext}`);

  if (minifiable && process.env.NODE_ENV === 'production') {
    const minPath = fullPath.slice(0, -ext.length) + `.min${ext}`;
    const needsMinify = !fs.existsSync(minPath) ||
      fs.statSync(fullPath).mtimeMs > fs.statSync(minPath).mtimeMs;

    if (needsMinify) {
      try {
        content = await minify(fullPath);
        fs.writeFileSync(minPath, content);
      } catch (e) {
        console.error('minify error:', e.message);
        content = fs.readFileSync(fullPath, 'utf8');
      }
    } else {
      content = fs.readFileSync(minPath, 'utf8');
    }
  } else {
    // Buffer, no 'utf8': leer una imagen como texto la corrompe.
    content = fs.readFileSync(fullPath);
  }

  res.writeHead(200, {
    'Content-Type': MIME[ext] || 'application/octet-stream',
    'Cache-Control': versionado ? 'public, max-age=31536000, immutable' : 'public, max-age=3600',
  });
  res.end(content);
}

module.exports = { serveStatic };
