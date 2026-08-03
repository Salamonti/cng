'use strict';
const assert = require('assert');
const path = require('path');
const m = require(path.join(__dirname, 'api_error_format.js'));

const pydantic =
    '{"detail":[{"loc":["body","password"],"msg":"Value error, Password must include upper, lower, digit, and symbol.","type":"value_error"}]}';
const out = m.formatApiErrorMessage(pydantic, { httpStatus: 422 });
assert(out.includes('12') || out.toLowerCase().includes('password'), out);

const login = '{"detail":"Invalid credentials"}';
assert.strictEqual(
    m.formatApiErrorMessage(login, { httpStatus: 401 }),
    'Incorrect email or password.'
);

const svc = m.summarizeServiceDetailForUser({
    service: 'asr',
    errors: ['Model timeout'],
});
assert(svc.includes('asr') && svc.includes('timeout'), svc);

console.log('api_error_format.test.cjs: ok');
