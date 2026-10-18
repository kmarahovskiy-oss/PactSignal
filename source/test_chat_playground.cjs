// Application handlers in a DOM stub. Not browser visual or end-to-end testing.
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class El{constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.style={};this.attrs={};this.listeners={};this._text=''}set textContent(v){this._text=String(v);this.children=[]}get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('')}append(...v){this.children.push(...v);if(this.tag==='select'&&!this.value&&v[0])this.value=v[0].value}replaceChildren(...v){this.children=v;this._text=''}setAttribute(k,v){this.attrs[k]=v}addEventListener(k,f){(this.listeners[k]??=[]).push(f)}get options(){return this.children}fire(k){if(this['on'+k])this['on'+k]();for(const f of this.listeners[k]||[])f()}}
const html=fs.readFileSync('dist/scenario.html','utf8'),nodes={};for(const m of html.matchAll(/id="([^"]+)"/g))nodes[m[1]]=new El();
for(const [k,v]of Object.entries(require('../dist/case-engine.js').defaults))nodes[k].value=String(v);nodes.client.value='Rowan';nodes['chat-time'].value='11:30';for(const [k,count]of [['extras',3],['deadline',2],['claimRef',3]])for(let i=0;i<count;i++)nodes[k].append(new El('option'));
const presets=['conflict','approve','withdraw'].map(p=>{const e=new El('button');e.dataset={preset:p};return e});const ctx={console,document:{getElementById:x=>nodes[x],createElement:x=>new El(x),createTextNode:x=>{const e=new El();e.textContent=x;return e},querySelectorAll:()=>presets,documentElement:{}},location:{hash:'#ru'},history:{replaceState(){}},fetch:async()=>({ok:true,json:async()=>JSON.parse(fs.readFileSync('dist/model.json','utf8'))})};ctx.window=ctx;vm.createContext(ctx);for(const file of ['case-engine.js','inference.js','context-analysis.js','chat-events.js','scenario.js','chat-ui.js'])vm.runInContext(fs.readFileSync('dist/'+file,'utf8'),ctx);
setImmediate(()=>{
 assert.equal(nodes.cards.children.length,2);
 nodes['guide-start'].fire('click');assert.equal(nodes['guide-content'].hidden,false);
 nodes['guide-steps'].children[0].children.at(-1).fire('click');
 const panel=nodes['chat-panels'].children[1];assert.match(panel.children[2].value,/approve EUR 180/);panel.children[4].fire('click');
 assert.equal(nodes['chat-log'].children.length,1);assert.equal(ctx.pactScenario.getInput().extras,'conditional');assert.equal(nodes.quote.disabled,true);assert.match(nodes['chat-log'].textContent,/300 → 480/);
 const entry=nodes['chat-log'].children[0];entry.children.find(x=>x.tag==='button').fire('click');assert.equal(ctx.pactScenario.getInput().extras,'approved');assert.match(nodes.cards.textContent,/480/);
 nodes.client.value='Morgan';nodes.client.fire('change');assert.equal(ctx.pactScenario.getInput().receipt,0);assert.match(nodes.cards.textContent,/HD-64/);assert.equal(nodes.quote.disabled,false);
 nodes.client.value='Rowan';nodes.client.fire('change');assert.equal(ctx.pactScenario.getInput().extras,'approved');assert.equal(nodes.quote.disabled,true);assert.equal(nodes['chat-log'].children.length,1);
 const wp=nodes['chat-panels'].children[2];wp.children[2].value='ST-25: I sent EUR 300 in total.';wp.children[4].fire('click');nodes['chat-log'].children[1].children.find(x=>x.tag==='button').fire('click');assert.equal(ctx.pactScenario.getInput().claimRef,'ST-25');assert.equal(ctx.PactCase.evaluate(ctx.pactScenario.getInput()).cases[1].receipt,0);
 nodes.reset.fire('click');assert.equal(ctx.pactScenario.getInput().extras,'approved');assert.equal(nodes['chat-log'].children.length,2);nodes.reset.fire('click');assert.equal(ctx.pactScenario.getInput().extras,'conditional');assert.equal(nodes.quote.disabled,false);
 nodes.lang.fire('click');assert.equal(ctx.document.documentElement.lang,'en');assert.equal(nodes['chat-heading'].textContent,'Messages from three channels');
 // Pending review survives client switches; an invalid baseline cannot start history.
 nodes.quote.value='-1';const badPanel=nodes['chat-panels'].children[1];badPanel.children[2].value='ST-24: I approve EUR 180';badPanel.children[4].fire('click');assert.equal(nodes.quote.disabled,false);assert.match(nodes['chat-status'].textContent,/Fix the initial/);nodes.quote.value='180';
 badPanel.children[4].fire('click');assert.equal(nodes.quote.disabled,true);nodes.client.value='Morgan';nodes.client.fire('change');const mp=nodes['chat-panels'].children[2];mp.children[2].value='hd-65: I sent EUR 300 in total.';mp.children[4].fire('click');nodes['chat-log'].children[0].children.find(x=>x.tag==='button').fire('click');assert.equal(ctx.pactScenario.getInput().claimRef,'ST-25');nodes.client.value='Rowan';nodes.client.fire('change');assert.equal(ctx.pactScenario.getInput().extras,'conditional');assert.ok(nodes['chat-log'].children[0].children.some(x=>x.tag==='button'));
 // Changing language cancels an armed destructive reset: another two explicit clicks are required.
 nodes.reset.fire('click');nodes.lang.fire('click');nodes.reset.fire('click');assert.equal(nodes.quote.disabled,true);nodes.reset.fire('click');assert.equal(nodes.quote.disabled,false);
 // Human interpretation of unsupported prose is two-stage and reaches the second order.
 const email=nodes['chat-panels'].children[0];email.children[1].value='manager';email.children[2].value='Please have Nina look after the portrait order while Alex is away.';email.children[4].fire('click');
 let row=nodes['chat-log'].children[0],editor=row.children.find(x=>x.className==='manual-editor');const fieldLabels=editor.children.filter(x=>x.tag==='label');fieldLabels[0].children[0].value='ST-25';fieldLabels[1].children[0].value='owner';fieldLabels[1].children[0].fire('change');fieldLabels[3].children[0].value='Nina';
 editor.children.find(x=>x.tag==='button').fire('click');assert.equal(ctx.pactScenario.getInput().second.owner,'Alex'); // checkbox required
 fieldLabels[4].children[0].checked=true;editor.children.find(x=>x.tag==='button').fire('click');assert.equal(ctx.pactScenario.getInput().second.owner,'Alex'); // preview is not application
 row=nodes['chat-log'].children[0];row.children.find(x=>x.tag==='button').fire('click');assert.equal(ctx.pactScenario.getInput().second.owner,'Nina');assert.equal(ctx.PactCase.evaluate(ctx.pactScenario.getInput()).cases[0].total,300);assert.match(nodes['session-summary'].textContent,/Подтверждено.*1|Confirmed.*1/);assert.match(nodes['session-summary'].textContent,/ST-25/);
 const telegram=nodes['chat-panels'].children[1];telegram.children[2].value='ST-25: I approve EUR 80 for the extra images.';telegram.children[4].fire('click');nodes['chat-log'].children[1].children.find(x=>x.tag==='button').fire('click');assert.equal(ctx.PactCase.evaluate(ctx.pactScenario.getInput()).cases[1].total,320);assert.equal(ctx.pactScenario.getInput().quote,180);
 nodes.client.value='Morgan';nodes.client.fire('change');assert.equal(ctx.pactScenario.getInput().second.owner,'Alex');assert.equal(ctx.PactCase.evaluate(ctx.pactScenario.getInput()).cases[1].total,240);nodes.client.value='Rowan';nodes.client.fire('change');assert.equal(ctx.pactScenario.getInput().second.owner,'Nina');assert.equal(ctx.PactCase.evaluate(ctx.pactScenario.getInput()).cases[1].total,320);
 // Source history must retain the exact human-selected mapping.
 assert.match(nodes.events.textContent,/ST-25.*Nina/);
 // Another client's explicit identifier must never expose a manual reassignment path.
 telegram.children[2].value='HD-64: I approve EUR 180 for the extra images.';telegram.children[4].fire('click');
 const foreign=nodes['chat-log'].children.at(-1);assert.ok(!foreign.children.some(x=>x.className==='manual-editor'));assert.ok(!foreign.children.some(x=>x.tag==='button'));assert.equal(ctx.pactScenario.getInput().extras,'conditional');
 // A repeated confirmed deadline keeps the agreement; show why nothing moved.
 email.children[2].value='ST-24: Thursday confirmed.';email.children[4].fire('click');nodes['chat-log'].children.at(-1).children.find(x=>x.tag==='button').fire('click');
 email.children[4].fire('click');const repeat=nodes['chat-log'].children.at(-1);assert.match(repeat.textContent,/не изменились|No change to agreed/);repeat.children.find(x=>x.tag==='button').fire('click');assert.equal(ctx.pactScenario.getInput().deadline,'accepted');
 assert.equal(nodes['guide-steps'].children.length,4);
 // Morgan starts with Alex: the guide must offer Nina, not an impossible no-op.
 nodes.client.value='Morgan';nodes.client.fire('change');nodes['guide-steps'].children[2].children.at(-1).fire('click');assert.match(email.children[2].value,/HD-64: Nina takes over/);
 console.log('PASS: guided fill, preview → confirmation, locked baseline, different client IDs, state restoration, isolated payment claim, explicit two-click reset, language switch (DOM stub)');
});
