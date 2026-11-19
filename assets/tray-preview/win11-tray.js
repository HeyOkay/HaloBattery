/* Windows 11 tray styling + published HaloBattery glyph/ring resources. */
(() => {
  'use strict';
  const base = new URL('.', document.currentScript.src);
  const css = new URL('win11-tray.css', base).href;
  const haloAssets = new URL('halobattery/', base);
  const haloKinds = {mouse:'Mouse',keyboard:'Keyboard',headset:'Headset',gamepad:'Gamepad',bluetooth:'Bluetooth device',microphone:'Microphone',speaker:'Speaker',webcam:'Webcam',health:'Health device',smart_home:'Smart home',watch:'Watch',display:'Display',tracker:'Tracker',xbox:'Xbox',playstation:'PlayStation',nintendo:'Nintendo Switch'};
  const kinds = Object.keys(haloKinds);
  const modes = {normal:'Discharging',charging:'Charging',low:'Low battery',warning:'Warning',full:'Fully charged',sleeping:'Sleeping',paused:'Paused / unknown',unknown_charging:'Charging · unknown level'};
  const sizes = [16,20,24,25,28,30,32,35,36,40,42,48,49,50,56,60,64,70,72,80,84,96,100,120,144];
  const nativeSize = target => sizes.reduce((a,b)=>Math.abs(b-target)<=Math.abs(a-target)?b:a);
  const icons = {up:'\ue70e',down:'\ue70d',wifi:'\ue701',offline:'\ue774',volume:'\ue995',quiet:'\ue74f',battery:'\ue83f',bell:'\uea8f',bluetooth:'\ue702',plane:'\ue709',sun:'\ue706',cloud:'\ue753',shield:'\uea18',sync:'\ue895',keyboard:'\ue765'};
  const glyph = name => `<span class="glyph" aria-hidden="true">${icons[name]||icons.cloud}</span>`;
  const haloKind = name => typeof name==='string'&&name.startsWith('halo:')&&Object.hasOwn(haloKinds,name.slice(5))?name.slice(5):null;
  const trayGlyph = name => {const kind=haloKind(name);return kind?`<canvas class="halo-icon" data-halo-kind="${kind}" width="16" height="16" aria-hidden="true"></canvas>`:glyph(name)};
  const atlasCache = new Map();
  const loadImage = url => new Promise((resolve,reject)=>{const img=new Image();img.onload=()=>resolve(img);img.onerror=()=>reject(new Error(`Cannot load HaloBattery artwork: ${url}`));img.src=url});
  function atlas(theme,size){
    const key=`${theme}-${size}`;
    if(!atlasCache.has(key)){
      const entry={ready:false,failed:false,waiters:new Set()};atlasCache.set(key,entry);
      Promise.all([loadImage(new URL(`rings-${key}.png`,haloAssets).href),loadImage(new URL(`glyphs-${key}.png`,haloAssets).href)]).then(([rings,glyphs])=>{entry.rings=rings;entry.glyphs=glyphs;entry.ready=true;entry.waiters.forEach(x=>x.queueHaloPaint());entry.waiters.clear()}).catch(error=>{entry.failed=true;entry.waiters.clear();console.error(error)});
    }
    return atlasCache.get(key);
  }
  function effective(state){
    const mode=state.mode;
    return {mode,level:mode==='low'?10:mode==='warning'?20:mode==='full'?100:['sleeping','paused','unknown_charging'].includes(mode)?null:state.level,active:mode!=='sleeping'};
  }
  function tile(ctx,image,index,size,target,columns=16){ctx.drawImage(image,index%columns*size,Math.floor(index/columns)*size,size,size,0,0,target,target)}

  class Win11Tray extends HTMLElement {
    static observedAttributes=['theme','language','notifications','seconds','datetime','time-zone','dpi'];
    constructor(){
      super();this.attachShadow({mode:'open'});this.panel=null;this.volume=67;this.wifi=true;this.airplane=false;
      this.deviceStates=Object.fromEntries(kinds.map(kind=>[kind,{mode:'normal',level:({mouse:47,keyboard:55,headset:69,gamepad:100})[kind]??75}]));
      this.pinnedIcons=['mouse','keyboard','headset','gamepad'].map(kind=>this.deviceItem(kind));
      this.trayIcons=kinds.filter(kind=>!['mouse','keyboard','headset','gamepad'].includes(kind)).map(kind=>this.deviceItem(kind));
      this._overflowPinned=[];this._haloFrame=0;this._lastHaloPaint=0;
      this._outside=e=>{if(!e.composedPath().includes(this))this.close()};
      this._escape=e=>{if(e.key==='Escape'&&this.panel){e.preventDefault();this.close(true)}};
      this._resize=()=>{this.syncPinnedOverflow();if(this.panel==='overflow')this.overflowContent(this.shadowRoot.querySelector('.flyout'));this.positionPanel();this.snapHaloPixels();this.queueHaloPaint();this.hideTip()};
      this._motion=()=>this.queueHaloPaint();
    }
    deviceItem(kind){return {id:`halo-${kind}`,label:`HaloBattery — ${haloKinds[kind]}`,icon:`halo:${kind}`}}
    connectedCallback(){
      this.style.setProperty('--tray-scale',this.scale);
      if(!this.hasAttribute('theme'))this.setAttribute('theme',matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
      this.render();this._timer=setInterval(()=>this.tick(),1000);
      document.addEventListener('pointerdown',this._outside);document.addEventListener('keydown',this._escape);window.addEventListener('resize',this._resize);
      this._motionQuery=matchMedia('(prefers-reduced-motion: reduce)');this._motionQuery.addEventListener('change',this._motion);
    }
    disconnectedCallback(){clearInterval(this._timer);clearTimeout(this._tipTimer);cancelAnimationFrame(this._haloFrame);this._haloFrame=0;document.removeEventListener('pointerdown',this._outside);document.removeEventListener('keydown',this._escape);window.removeEventListener('resize',this._resize);this._motionQuery?.removeEventListener('change',this._motion);this._animation?.cancel();atlasCache.forEach(x=>x.waiters.delete(this))}
    attributeChangedCallback(name){
      if(name==='dpi')this.style.setProperty('--tray-scale',this.scale);
      if(!this.isConnected||!this.shadowRoot.querySelector('.surface'))return;
      if(['language','notifications'].includes(name)){this.close(false,false);this.render()}
      if(name==='dpi'){this.syncPinnedOverflow();if(this.panel==='overflow')this.overflowContent(this.shadowRoot.querySelector('.flyout'));this.positionPanel();this.snapHaloPixels();this.hideTip()}
      if(name==='dpi'||name==='theme')this.queueHaloPaint();this.tick();
    }
    get scale(){const n=Number(this.getAttribute('dpi')||96);return Math.min(384,Math.max(48,Number.isFinite(n)&&n>0?n:96))/96}
    emit(name,detail={}){this.dispatchEvent(new CustomEvent(`win11:${name}`,{detail,bubbles:true,composed:true}))}
    get now(){const value=this.getAttribute('datetime');const date=value?new Date(value):new Date();return Number.isNaN(date.getTime())?new Date():date}
    get dateOptions(){const timeZone=this.getAttribute('time-zone');if(!timeZone)return {};try{new Intl.DateTimeFormat('en-US',{timeZone});return {timeZone}}catch{return {}}}
    format(date,options){return new Intl.DateTimeFormat('en-US',{...this.dateOptions,...options}).format(date)}
    render(){
      const language=this.getAttribute('language')==='ru-RU'?'RUS':'ENG';
      this.shadowRoot.innerHTML=`<link rel="stylesheet" href="${css}"><div class="surface" part="surface"><div class="material" part="material"></div>
      <button class="tray-button" data-panel="overflow" aria-label="Show hidden icons" aria-haspopup="dialog" aria-controls="tray-panel" aria-expanded="false" data-tip="Show hidden icons">${glyph('up')}</button>
      <div class="pinned-icons" part="device-icons"></div>
      <button class="tray-button language" data-panel="langs" aria-label="Input language" aria-haspopup="dialog" aria-controls="tray-panel" aria-expanded="false" data-tip="Input language">${language}</button>
      <button class="tray-button omni control-center" aria-label="Network and sound"><span class="icon-cell network">${glyph(this.airplane?'plane':this.wifi?'wifi':'offline')}</span><span class="icon-cell sound">${glyph(this.volume?'volume':'quiet')}</span></button>
      <button class="tray-button omni notification-center" aria-label="Date and time"><span class="clock"><span class="time"></span><br><span class="date"></span></span>${this.getAttribute('notifications')!=='false'?`<span class="icon-cell">${glyph('bell')}</span>`:''}</button>
      <button class="desktop" aria-label="Show desktop" data-tip="Show desktop"></button></div>
      <div class="flyout" id="tray-panel" role="dialog" hidden></div><div class="tip" role="tooltip" hidden></div>`;
      const root=this.shadowRoot;
      const layout=()=>{if(!this.isConnected)return;this.syncPinnedOverflow();this.positionPanel();this.snapHaloPixels();this.queueHaloPaint()};
      root.querySelector('link').addEventListener('load',layout,{once:true});document.fonts.ready.then(layout);
      this.pinnedIcons.forEach(item=>root.querySelector('.pinned-icons').append(this.iconButton(item,true)));
      root.querySelectorAll('[data-panel]').forEach(b=>b.addEventListener('click',()=>this.toggle(b.dataset.panel,b)));
      root.querySelector('.control-center').addEventListener('click',()=>this.emit('controlclick',{id:'network'}));
      root.querySelector('.notification-center').addEventListener('click',()=>this.emit('controlclick',{id:'clock'}));
      root.querySelector('.desktop').addEventListener('click',()=>{this.close();this.emit('showdesktop')});
      root.querySelectorAll('.tray-button,.desktop').forEach(b=>this.tooltipEvents(b));
      this.tick();this.refreshDeviceTips();layout();
    }
    iconButton(item,pinned=false){const b=document.createElement('button');if(pinned)b.className='tray-button halo-device';b.setAttribute('aria-label',item.label);b.dataset.baseLabel=item.label;const kind=haloKind(item.icon);if(kind)b.dataset.haloDevice=kind;b.dataset.tip=item.label;b.innerHTML=trayGlyph(item.icon);b.addEventListener('click',()=>this.emit('trayclick',{id:item.id}));if(!pinned)this.tooltipEvents(b);return b}
    tooltipEvents(b){b.addEventListener('pointerenter',()=>this.showTip(b));b.addEventListener('pointerleave',()=>this.hideTip());b.addEventListener('pointerdown',()=>this.hideTip());b.addEventListener('focus',()=>this.showTip(b));b.addEventListener('blur',()=>this.hideTip())}
    syncPinnedOverflow(){const buttons=[...this.shadowRoot.querySelectorAll('.pinned-icons button')];if(!buttons.length)return;const base=this.offsetWidth-buttons.reduce((n,b)=>n+(b.hidden?0:b.offsetWidth),0);const count=Math.max(0,Math.min(buttons.length,Math.floor((document.documentElement.clientWidth/this.scale-base)/32)));buttons.forEach((b,i)=>b.hidden=i>=count);this._overflowPinned=this.pinnedIcons.slice(count)}
    snapHaloPixels(){const dpr=window.devicePixelRatio||1;this.shadowRoot.querySelectorAll('.halo-icon').forEach(c=>{c.style.transform='';if(!c.getClientRects().length)return;const r=c.getBoundingClientRect();c.style.transform=`translate(${(Math.round(r.left*dpr)/dpr-r.left)/this.scale}px,${(Math.round(r.top*dpr)/dpr-r.top)/this.scale}px)`})}
    tick(){const root=this.shadowRoot;if(!root.querySelector('.time'))return;const d=this.now;root.querySelector('.time').textContent=this.format(d,{hour:'2-digit',minute:'2-digit',hourCycle:'h23',...(this.hasAttribute('seconds')?{second:'2-digit'}:{})});root.querySelector('.date').textContent=new Intl.DateTimeFormat('en-GB',{...this.dateOptions,day:'2-digit',month:'2-digit',year:'numeric'}).format(d).replaceAll('/','.');const full=this.format(d,{weekday:'long',day:'numeric',month:'long',year:'numeric'});root.querySelector('.notification-center').setAttribute('aria-label',full);root.querySelector('.notification-center').dataset.tip=full;root.querySelector('.control-center').dataset.tip=`${this.wifi?'Wi-Fi: connected':'Wi-Fi: disconnected'}\nSpeakers: ${this.volume}%`;this.snapHaloPixels()}
    getDeviceKinds(){return {...haloKinds}}
    getDeviceModes(){return {...modes}}
    getDeviceState(kind){if(!Object.hasOwn(haloKinds,kind))throw new RangeError('Unknown device kind');return {...this.deviceStates[kind]}}
    setDeviceState(kind,patch){const state=this.getDeviceState(kind);if(patch.mode!==undefined){if(!Object.hasOwn(modes,patch.mode))throw new RangeError('Unknown device state');state.mode=patch.mode}if(Number.isFinite(patch.level))state.level=Math.round(Math.max(0,Math.min(100,patch.level)));this.deviceStates[kind]=state;this.refreshDeviceTips();this.queueHaloPaint();this.emit('devicechange',{kind,...state})}
    isDevicePinned(kind){return this.pinnedIcons.some(x=>haloKind(x.icon)===kind)}
    setDevicePinned(kind,pinned){this.getDeviceState(kind);const icon=`halo:${kind}`;const item=this.pinnedIcons.find(x=>x.icon===icon)||this.trayIcons.find(x=>x.icon===icon)||this.deviceItem(kind);this.pinnedIcons=this.pinnedIcons.filter(x=>x.icon!==icon);this.trayIcons=this.trayIcons.filter(x=>x.icon!==icon);(pinned?this.pinnedIcons:this.trayIcons).push(item);this.close(false,false);this.render();this.emit('devicepinchange',{kind,pinned:!!pinned})}
    refreshDeviceTips(){this.shadowRoot.querySelectorAll('[data-halo-device]').forEach(b=>{const state=effective(this.deviceStates[b.dataset.haloDevice]);b.dataset.tip=`${b.dataset.baseLabel}\n${state.level===null?'Unknown battery level':state.level+'%'} · ${modes[state.mode]}`})}
    queueHaloPaint(){if(!this.isConnected||this._haloFrame)return;this._haloFrame=requestAnimationFrame(time=>{this._haloFrame=0;this.paintHaloIcons(time)})}
    paintHaloIcons(time){
      if(time-this._lastHaloPaint<1000/30){this.queueHaloPaint();return}this._lastHaloPaint=time;
      const motion=!matchMedia('(prefers-reduced-motion: reduce)').matches;let animate=false;
      const theme=this.getAttribute('theme')==='light'?'light':'dark';const target=Math.max(1,Math.round(16*this.scale*(window.devicePixelRatio||1)));const size=nativeSize(target);const images=atlas(theme,size);
      if(!images.ready){if(!images.failed)images.waiters.add(this);return}
      this.shadowRoot.querySelectorAll('canvas[data-halo-kind]').forEach(canvas=>{
        if(!canvas.getClientRects().length)return;
        if(canvas.width!==target||canvas.height!==target){canvas.width=target;canvas.height=target}
        const kind=canvas.dataset.haloKind,state=effective(this.deviceStates[kind]);const ctx=canvas.getContext('2d');ctx.clearRect(0,0,target,target);ctx.imageSmoothingEnabled=size!==target;ctx.imageSmoothingQuality='high';ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';
        if(state.mode==='charging'){
          const phase=(time%3000)/3000;const pulse=motion?Math.floor(255*(.12+.88*Math.pow(.5+.5*Math.cos(phase*2*Math.PI),.7)))/255:1;
          ctx.globalAlpha=1-pulse;tile(ctx,images.rings,101+state.level,size,target);ctx.globalCompositeOperation='lighter';ctx.globalAlpha=pulse;tile(ctx,images.rings,202+state.level,size,target);animate=motion;
        }else{const frame=state.mode==='sleeping'?393:state.mode==='paused'?394:state.mode==='unknown_charging'?303+(motion?Math.floor((time%3000)/3000*90):0):state.level;tile(ctx,images.rings,frame,size,target);if(state.mode==='unknown_charging')animate=motion||animate}
        ctx.globalAlpha=1;ctx.globalCompositeOperation='source-over';tile(ctx,images.glyphs,kinds.indexOf(kind)+(state.active?0:16),size,target);
      });
      if(animate)this.queueHaloPaint();
    }
    showTip(button){this.hideTip();if(this.panel&&this.panel!=='overflow')return;this._tipTimer=setTimeout(()=>{if(!button.isConnected)return;const tip=this.shadowRoot.querySelector('.tip');tip.textContent=button.dataset.tip||button.getAttribute('aria-label');tip.hidden=false;const r=button.getBoundingClientRect(),h=this.getBoundingClientRect(),s=this.scale,vw=document.documentElement.clientWidth;tip.style.maxWidth=`${Math.min(320,vw/s-16)}px`;tip.style.bottom=`${this.panel==='overflow'?60+(this.shadowRoot.querySelector('.flyout').offsetHeight+12):60}px`;const width=tip.offsetWidth*s;const x=Math.max(8,Math.min(vw-width-8,r.x+r.width/2-width/2));tip.style.left=`${(x-h.left)/s}px`;},700)}
    hideTip(){clearTimeout(this._tipTimer);const t=this.shadowRoot.querySelector('.tip');if(t)t.hidden=true}
    toggle(name,button){if(!['overflow','langs'].includes(name))return;if(this.panel===name){this.close();return}this.close(false,false);this.panel=name;this._trigger=button;button.setAttribute('aria-expanded','true');this.hideTip();const p=this.shadowRoot.querySelector('.flyout');p.className=`flyout ${name}`;p.inert=false;p.hidden=false;p.setAttribute('aria-label',name==='overflow'?'Hidden icons':'Input language');this[`${name}Content`](p);this.positionPanel();this.refreshDeviceTips();this.snapHaloPixels();this.queueHaloPaint();if(!matchMedia('(prefers-reduced-motion: reduce)').matches)this._animation=p.animate([{opacity:0,transform:'translateY(16px)'},{opacity:1,transform:'translateY(0)'}],{duration:200,easing:'cubic-bezier(.1,.9,.2,1)'});this.emit('panelchange',{panel:name})}
    close(returnFocus=false,animate=true){const p=this.shadowRoot.querySelector('.flyout');if(!p)return;this._animation?.cancel();const was=this.panel;this.panel=null;p.inert=true;if(was&&animate&&!matchMedia('(prefers-reduced-motion: reduce)').matches){this._animation=p.animate([{opacity:1,transform:'translateY(0)'},{opacity:0,transform:'translateY(12px)'}],{duration:120,easing:'cubic-bezier(.7,0,1,.5)'});this._animation.onfinish=()=>{if(!this.panel)p.hidden=true}}else p.hidden=true;this.shadowRoot.querySelectorAll('[aria-expanded=true]').forEach(b=>b.setAttribute('aria-expanded','false'));this.hideTip();if(returnFocus)this._trigger?.focus();if(was)this.emit('panelchange',{panel:null})}
    positionPanel(){if(!this.panel)return;const p=this.shadowRoot.querySelector('.flyout'),r=this._trigger.getBoundingClientRect(),h=this.getBoundingClientRect(),s=this.scale,vw=document.documentElement.clientWidth,vh=document.documentElement.clientHeight;const margin=12*s;p.style.maxWidth=`${Math.max(60,vw/s-24)}px`;p.style.maxHeight=`${Math.max(60,vh/s-72)}px`;p.style.bottom='60px';const width=p.offsetWidth*s;const desired=this.panel==='overflow'?r.x+r.width/2-width/2:vw-margin-width;const x=Math.max(margin,Math.min(vw-width-margin,desired));p.style.left=`${(x-h.left)/s}px`;p.style.right='auto'}
    overflowContent(p){p.replaceChildren();const items=[...this._overflowPinned,...this.trayIcons];const columns=Math.max(1,Math.min(5,items.length,Math.floor((document.documentElement.clientWidth/this.scale-34)/40)));p.style.gridTemplateColumns=`repeat(${columns},40px)`;items.forEach(item=>p.append(this.iconButton(item)));this.refreshDeviceTips();this.queueHaloPaint()}
    langsContent(p){p.innerHTML=`<h2>Keyboard layout</h2><div role="listbox" aria-label="Input language"></div><footer>Input language applies within this website</footer>`;[['ru-RU','RUS','Russian','Russian'],['en-US','ENG','English (United States)','US']].forEach(([id,code,title,subtitle])=>{const b=document.createElement('button');b.setAttribute('role','option');b.setAttribute('aria-selected',String((this.getAttribute('language')||'en-US')===id));b.innerHTML=`<span class="lang-code">${code}</span><span>${title}<small>${subtitle}</small></span>`;b.addEventListener('click',()=>{this.setAttribute('language',id);this.emit('languagechange',{language:id});this.shadowRoot.querySelector('.language').focus()});p.querySelector('[role=listbox]').append(b)})}
    setStatus(values){if(typeof values.wifi==='boolean')this.wifi=values.wifi;if(typeof values.airplane==='boolean')this.airplane=values.airplane;if(Number.isFinite(values.volume))this.volume=Math.max(0,Math.min(100,values.volume));this.updateIcons()}
    setTrayIcons(items){this.trayIcons=items.filter(x=>x&&typeof x.id==='string'&&typeof x.label==='string').map(x=>({...x}));if(this.panel==='overflow'){this.overflowContent(this.shadowRoot.querySelector('.flyout'));this.positionPanel();this.snapHaloPixels()}}
    setPinnedIcons(items){this.pinnedIcons=items.filter(x=>x&&typeof x.id==='string'&&typeof x.label==='string').map(x=>({...x}));this.close(false,false);this.render()}
    updateIcons(){if(!this.shadowRoot.querySelector('.network'))return;this.shadowRoot.querySelector('.network').innerHTML=glyph(this.airplane?'plane':this.wifi?'wifi':'offline');this.shadowRoot.querySelector('.sound').innerHTML=glyph(this.volume?'volume':'quiet');this.tick()}
  }
  if(!customElements.get('win11-tray'))customElements.define('win11-tray',Win11Tray);
})();
