// A four-file static server for the probe pages. AudioWorklet.addModule needs a
// same-origin http(s) URL, so file:// will not do.
const http = require('http');
const fs = require('fs');
const path = require('path');

const port = Number(process.argv[2]) || 8731;
const types = { '.html': 'text/html', '.js': 'text/javascript', '.wasm': 'application/wasm' };

http.createServer((req, res) => {
  if (req.method === 'POST' && (req.url === '/result' || req.url === '/result-rt')) {
    let body = '';
    req.on('data', d => { body += d; });
    req.on('end', () => {
      const name = req.url === '/result-rt' ? 'results-rt.json' : 'results.json';
      fs.writeFileSync(path.join(__dirname, name), body);
      res.writeHead(200);
      res.end('ok');
      console.log('wrote', name, body.length, 'bytes');
    });
    return;
  }
  let p = req.url.split('?')[0];
  if (p === '/') p = '/offline.html';
  const f = path.join(__dirname, path.normalize(p).replace(/^[\\/]+/, ''));
  if (!f.startsWith(__dirname)) { res.writeHead(403); res.end(); return; }
  fs.readFile(f, (err, data) => {
    if (err) { res.writeHead(404); res.end('not found'); return; }
    res.writeHead(200, { 'content-type': types[path.extname(f)] || 'application/octet-stream' });
    res.end(data);
  });
}).listen(port, '127.0.0.1', () => console.log('listening on', port));
