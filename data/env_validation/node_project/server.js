const http = require('http');

const server = http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify({ status: 'ok', ecosystem: 'Node.js' }));
});

if (require.main === module) {
  server.listen(3099, () => console.log('Node Server Running on 3099'));
} else {
  module.exports = server;
}
