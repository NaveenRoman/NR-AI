const assert = require('assert');
const http = require('http');
const server = require('./server');

server.listen(3098, () => {
  http.get('http://127.0.0.1:3098', (res) => {
    assert.strictEqual(res.statusCode, 200);
    let raw = '';
    res.on('data', c => raw += c);
    res.on('end', () => {
      const data = JSON.parse(raw);
      assert.strictEqual(data.status, 'ok');
      console.log('NODE TEST PASSED');
      server.close();
      process.exit(0);
    });
  });
});
