const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
function boot(){
// Application handlers in a DOM stub. Not browser visual or end-to-end testing.

class El{constructor(tag='div'){this.tag=tag;this.children=[];this.value='';this.style={};this.attrs={};this.listeners={};this._text=''}set textContent(v){this._text=String(v);this.children=[]}get textContent(){return this._text+this.children.map(c=>c.textContent||'').join('')}append(...v){this.children.push(...v);if(this.tag==='select'&&!this.value&&v[0])this.value=v[0].value}replaceChildren(...v){this.children=v;this._text=''}setAttribute(k,v){this.attrs[k]=v}addEventListener(k,f){(this.listeners[k]??=[]).push(f)}get options(){return this.children}fire(k){if(this['on'+k])this['on'+k]();for(const f of this.listeners[k]||[])f()}}
const html=fs.readFileSync('dist/scenario.html','utf8'),nodes={};for(const m of html.matchAll(/id="([^"]+)"/g))nodes[m[1]]=new El();
for(const [k,v]of Object.entries(require('../dist/case-engine.js').defaults))nodes[k].value=String(v);nodes.client.value='Rowan';nodes['chat-time'].value='11:30';for(const [k,count]of [['extras',3],['deadline',2],['claimRef',3]])for(let i=0;i<count;i++)nodes[k].append(new El('option'));
const presets=['conflict','approve','withdraw'].map(p=>{const e=new El('button');e.dataset={preset:p};return e});const ctx={console,document:{getElementById:x=>nodes[x],createElement:x=>new El(x),createTextNode:x=>{const e=new El();e.textContent=x;return e},querySelectorAll:()=>presets,documentElement:{}},location:{hash:'#ru'},history:{replaceState(){}},fetch:async()=>({ok:true,json:async()=>JSON.parse(fs.readFileSync('dist/model.json','utf8'))})};ctx.window=ctx;vm.createContext(ctx);for(const file of ['case-engine.js','inference.js','context-analysis.js','chat-events.js','scenario.js','chat-ui.js'])vm.runInContext(fs.readFileSync('dist/'+file,'utf8'),ctx);

return {nodes,ctx};}

const results=[];
function test(name,fn){try{fn();results.push({name,passed:true})}catch(e){results.push({name,passed:false,error:e.stack})}}
function send(b,ch,role,text){const p=b.nodes['chat-panels'].children[ch];p.children[1].value=role;p.children[2].value=text;p.children[4].fire('click');return b.nodes['chat-log'].children.at(-1)}
function confirm(row){const btn=row.children.find(x=>x.tag==='button');assert.ok(btn,'Expected a confirmation button');btn.fire('click')}
const actions=[
 ['approve','client',id=>`${id}: I approve EUR ${id==='ST-24'?180:80} for the extra images.`,(b,id)=>assert.equal(b.ctx.PactCase.evaluate(b.ctx.pactScenario.getInput()).cases[id==='ST-24'?0:1].total,id==='ST-24'?480:320)],
 ['withdraw','client',id=>`${id}: Withdraw my extra-image request.`,(b,id)=>assert.equal(id==='ST-24'?b.ctx.pactScenario.getInput().extras:b.ctx.pactScenario.getInput().second.extras,'withdrawn')],
 ['claim','client',id=>`${id}: I sent EUR 300 in total.`,(b,id)=>assert.equal(b.ctx.pactScenario.getInput().claimRef,id)],
 ['request_date','client',id=>`${id}: Thursday please`,(b,id)=>assert.equal(b.ctx.PactCase.evaluate(b.ctx.pactScenario.getInput()).cases[id==='ST-24'?0:1].date,id==='ST-24'?'Friday':'Monday')],
 ['confirm_date','manager',id=>`${id}: Thursday confirmed.`,(b,id)=>assert.equal(b.ctx.PactCase.evaluate(b.ctx.pactScenario.getInput()).cases[id==='ST-24'?0:1].date,'Thursday')],
 ['owner','manager',id=>`${id}: ${id==='ST-24'?'Alex':'Nina'} takes over as owner.`,(b,id)=>assert.equal(b.ctx.PactCase.evaluate(b.ctx.pactScenario.getInput()).cases[id==='ST-24'?0:1].owner,id==='ST-24'?'Alex':'Nina')]
];
for(const [action,role,message,check] of actions)for(const id of ['ST-24','ST-25'])for(let ch=0;ch<3;ch++)test(`${action} / ${id} / ${['Email','Telegram','WhatsApp'][ch]}`,()=>{
 const b=boot(),before=JSON.stringify(b.ctx.pactScenario.getInput());
 const row=send(b,ch,role,message(id));assert.equal(JSON.stringify(b.ctx.pactScenario.getInput()),before,'Unconfirmed text mutated case');confirm(row);check(b,id);
 const expected=JSON.stringify(b.ctx.pactScenario.getInput());confirm(send(b,ch,role,message(id)));assert.equal(JSON.stringify(b.ctx.pactScenario.getInput()),expected,'Repeat changed state');
 assert.equal(b.ctx.PactCase.evaluate(b.ctx.pactScenario.getInput()).cases[0].receipt,100);assert.equal(b.ctx.PactCase.evaluate(b.ctx.pactScenario.getInput()).cases[1].receipt,0);
 b.nodes.client.value='Morgan';b.nodes.client.fire('change');assert.equal(b.ctx.pactScenario.getInput().receipt,0);b.nodes.client.value='Rowan';b.nodes.client.fire('change');assert.equal(JSON.stringify(b.ctx.pactScenario.getInput()),expected);
 b.nodes.lang.fire('click');assert.equal(JSON.stringify(b.ctx.pactScenario.getInput()),expected);assert.equal(b.nodes['chat-log'].children.length,2);
});
const unsafe=[
 ['negated approval','client','ST-24: I do not approve EUR 180.'],
 ['conditional approval','client','ST-24: I approve EUR 180 if the client agrees.'],
 ['quoted approval','client','ST-24: "I approve EUR 180"'],
 ['wrong currency','client','ST-24: I approve USD 180.'],
 ['missing currency','client','ST-24: I approve 180.'],
 ['wrong amount','client','ST-24: I approve EUR 179.'],
 ['wrong sender','manager','ST-24: I approve EUR 180.'],
 ['client cannot confirm studio deadline','client','ST-24: Thursday confirmed.'],
 ['client cannot appoint manager','client','ST-24: Alex takes over as owner.'],
 ['unrecognized payment amount','client','ST-24: I sent EUR 301 in total.'],
 ['combined actions','client','ST-24: I approve EUR 180 and I sent EUR 300.'],
 ['other client order','client','HD-64: I approve EUR 180.'],
 ['unknown order','client','ST-26: I approve EUR 180.'],
 ['script-like input','client','<script>alert(1)</script> ST-24: I approve EUR 180.']
];
for(const [name,role,text]of unsafe)test(name,()=>{const b=boot(),before=JSON.stringify(b.ctx.pactScenario.getInput()),row=send(b,1,role,text);assert.equal(JSON.stringify(b.ctx.pactScenario.getInput()),before);assert.ok(!row.children.some(x=>x.tag==='button'),'Unsafe confirmation offered');assert.ok(row.textContent.includes(text),'Original text not preserved');if(name==='other client order')assert.ok(!row.children.some(x=>x.className==='manual-editor'));});
const report={type:'Automated user-workflow simulation using actual application handlers and a DOM stub',limitations:['Not browser rendering or real clicks','Not an independent human test','Synthetic inputs; not model accuracy evaluation'],scenarios:results.length,passed:results.filter(x=>x.passed).length,results};
fs.writeFileSync('source/judge-acceptance-50.json',JSON.stringify(report,null,2)+'\n');console.log(JSON.stringify(report,null,2));if(report.passed!==report.scenarios)process.exitCode=1;
