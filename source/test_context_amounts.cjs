const assert=require('assert/strict'),c=require('../dist/context-analysis.js');let n=0;
const valid=[['EUR 1,200.50',1200.5],['1.200,50 EUR',1200.5],['EUR 300',300],['300 EUR',300],['€ 180,50',180.5],['USD 2,345.60',2345.6],['GBP 0.50',0.5],['EUR 1 200,50',1200.5],['EUR 0',0]];
for(const [amount,value] of valid){const a=c.analyze('I will pay '+amount+' tomorrow.');assert.equal(a.events[0].amounts[0].value,value,amount);assert.equal(a.confirmedReceived,0);n++}
for(const amount of ['EUR 1,200','EUR 1.200','EUR 12,34.50','EUR -300','EUR 30 50','EUR 12345678901']){const a=c.analyze('I will pay '+amount+' tomorrow.');assert.equal(a.events[0].amounts.length,0,amount);assert.ok(a.warnings.length);n++}
for(const t of ['If we get funding we approve the quote but please proceed.','We approve the quote unless the supplier declines.','"We approve the quote.']){assert.equal(c.analyze(t).events.length,0,t);n++}
for(const t of ['The bank says "payment completed" but the funds were reversed.','The bank says "payment completed", however the transfer failed.']){assert.equal(c.analyze(t).status,'payment_failure');n++}
const a=c.analyze('I paid EUR 300 for ST-24 and ST-25.');assert.ok(a.warnings.some(x=>x.includes('Multiple orders')));n++;
assert.equal(c.amountsIn('ST-24 EUR 300').amounts[0].value,300);n++;
console.log(n+' amount and clause regression checks passed.');
