src = open('Main.dc.html').read()

b = src
b = b.replace('<div style="width:1920px;height:1080px;background:#0C1211;display:flex;flex-direction:column;padding:68px 80px 60px">',
 '<div style="width:1920px;height:1080px;background:#0C1211;position:relative;overflow:hidden">\n'
 '<div style="width:1920px;height:1080px;display:flex;flex-direction:column;padding:68px 80px 60px;opacity:.2">')
b = b.replace('<div class="lbl" style="font-size:13px;color:#E0913F">Practising</div>',
              '<div class="lbl" style="font-size:13px;color:#E0913F">Lead-in</div>')
b = b.replace('left:62%;width:2px;background:#F6C98A', 'left:0%;width:2px;background:#F6C98A')
b = b.replace('left:62%;margin-left:-6px', 'left:0%;margin-left:0')
b = b.replace('clip-path="inset(0 38% 0 0)"', 'clip-path="inset(0 100% 0 0)"')
b = b.replace('stroke-dashoffset="325"', 'stroke-dashoffset="854"')
b = b.replace('>62%</text>', '>0%</text>')
overlay = '''
</div>

<div style="position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px">
  <div class="lbl" style="font-size:18px;color:#8A5C29;letter-spacing:.34em">Lead-in</div>
  <div class="num" style="font-size:520px;color:#E0913F;line-height:.9">2</div>
  <div style="display:flex;gap:14px;align-items:center;margin-top:14px">
    <div style="width:64px;height:6px;border-radius:3px;background:#8A5C29"></div>
    <div style="width:64px;height:6px;border-radius:3px;background:#E0913F"></div>
    <div style="width:64px;height:6px;border-radius:3px;background:#1C2523"></div>
    <div style="width:64px;height:6px;border-radius:3px;background:#1C2523"></div>
  </div>
  <div class="mono" style="font-size:20px;color:#6A7873;letter-spacing:.06em;margin-top:22px">4 bars, then bar 63.1</div>
</div>
</div>'''
b = b.replace('</div>\n</x-dc>', overlay + '\n</x-dc>')
open('PracticeLeadIn.dc.html','w').write(b)

c = src
c = c.replace('<div class="lbl" style="font-size:13px;color:#E0913F">Practising</div>',
              '<div class="lbl" style="font-size:13px;color:#5FA88F">Ladder advanced</div>')
c = c.replace('<div class="lbl" style="font-size:13px">Speed</div>',
 '<div style="display:flex;align-items:baseline;gap:14px">'
 '<div class="lbl" style="font-size:13px">Speed</div>'
 '<div class="mono num" style="font-size:15px;color:#6A7873;text-decoration:line-through;font-weight:400">55%</div>'
 '<div class="mono" style="font-size:15px;color:#5FA88F;letter-spacing:.02em">+5</div></div>')
c = c.replace('<div class="num" style="font-size:236px;color:#E0913F">55%</div>',
 '<div class="num" style="font-size:236px;color:#E0913F;text-shadow:0 0 90px rgba(224,145,63,.34)">60%</div>')
c = c.replace('50 bpm &nbsp;·&nbsp; 91.5 at full speed', '55 bpm &nbsp;·&nbsp; 91.5 at full speed')
c = c.replace('38.2 s AT 55%', '35.0 s AT 60%')
c = c.replace('<div class="num" style="font-size:236px;color:#E8EEEB;font-weight:600">25</div>',
              '<div class="num" style="font-size:236px;color:#4C635C;font-weight:600">0</div>')
c = c.replace('2 of 3 clean to advance', 'counter reset at the new rung')
c = c.replace('stroke-dashoffset="325"', 'stroke-dashoffset="854"')
c = c.replace('>62%</text>', '>0%</text>')
c = c.replace('clip-path="inset(0 38% 0 0)"', 'clip-path="inset(0 100% 0 0)"')
c = c.replace('left:62%;width:2px;background:#F6C98A', 'left:0%;width:2px;background:#F6C98A')
c = c.replace('left:62%;margin-left:-6px', 'left:0%;margin-left:0')
c = c.replace(
 '''<div style="width:34px;height:1px;background:#8A5C29"></div>
    <div style="font-size:27px;color:#9CAAA4">Next rung
      <span style="color:#E0913F;font-weight:600">60%</span> after 1 more clean rep</div>''',
 '''<div style="width:34px;height:1px;background:#5FA88F"></div>
    <div style="font-size:27px;color:#9CAAA4">Three clean reps at
      <span class="mono" style="color:#E8EEEB">55%</span> &nbsp;·&nbsp; earned at 19:44 &nbsp;·&nbsp;
      <span style="color:#9CAAA4">next rung</span> <span style="color:#E0913F;font-weight:600">65%</span></div>''')
open('PracticeAdvance.dc.html','w').write(c)

import re
for f in ['Main.dc.html','PracticeLeadIn.dc.html','PracticeAdvance.dc.html']:
    s=open(f).read(); body=s[s.index('<x-dc>')+6:s.index('</x-dc>')]
    o=len(re.findall(r'<div\b',body)); cl=len(re.findall(r'</div>',body))
    print(f,o,cl,'ok' if o==cl else 'MISMATCH')
