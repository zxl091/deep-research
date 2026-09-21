"""固定绘图模板：模型只提供 JSON 数据，所有图片在已有隔离容器中执行。"""
import json


def render_code(spec):
    return 'spec = json.loads(' + repr(json.dumps(spec, ensure_ascii=False)) + ')\n' + TEMPLATE


TEMPLATE = r'''
rows, kind = spec['points'], spec['type']
labels = list(dict.fromkeys(p['label'] for p in rows))
series = list(dict.fromkeys(p.get('series', '') for p in rows))
palette = ['#35658D', '#E39D3D', '#44988D', '#A36AA5', '#CB6666', '#798796', '#B8A441', '#6B85B7']
colors = [palette[i % len(palette)] for i in range(max(len(series), len(labels)))]
fig, ax = plt.subplots(figsize=(10.5, 6), subplot_kw={'projection': 'polar'} if kind == 'radar' else {})
unit = rows[0]['unit']
def display(p):
    prefix = {'at_least':'≥', 'at_most':'≤', 'more_than':'>', 'less_than':'<', 'approximate':'≈'}.get(p.get('qualifier'), '')
    return prefix + format(p['value'], '.6g')
def tpos(period):
    if '-H' in period:
        y, h = period.split('-H'); return int(y)+(int(h)-1)/2
    if '-Q' in period:
        y, q = period.split('-Q'); return int(y)+(int(q)-1)/4
    if '-' in period:
        y, m = period.split('-'); return int(y)+(int(m)-1)/12
    return int(period)
if kind == 'line':
    periods = sorted({p['period'] for p in rows}, key=tpos)
    for i, name in enumerate(series):
        points = [p for p in rows if p.get('series', '') == name]
        xs, ys = [tpos(p['period']) for p in points], [p['value'] for p in points]
        ax.plot(xs, ys, marker='o', linestyle='-' if len(points)>2 else 'None',
                linewidth=2, markersize=5, color=colors[i], label=name or rows[0]['metric'])
        if len(points) <= 15:
            for x,p in zip(xs, points): ax.annotate(display(p),(x,p['value']),xytext=(0,7),textcoords='offset points',ha='center',fontsize=8)
    step = max(1, (len(periods)+14)//15)
    ticks = list(dict.fromkeys(periods[::step]+periods[-1:]))
    ax.set_xticks([tpos(t) for t in ticks], ticks, rotation=25 if len(ticks)>6 else 0)
    ax.set_ylabel(unit)
    if len(series)>1: ax.legend(frameon=False)
elif kind in ('bar', 'horizontal_bar'):
    if kind=='horizontal_bar':
        rows = sorted(rows, key=lambda p:p['value'])
        ax.barh(range(len(rows)),[p['value'] for p in rows],color=palette[0],height=.65)
        ax.set_yticks(range(len(rows)),[p['label'] for p in rows]);ax.set_xlabel(unit)
        for i,p in enumerate(rows): ax.annotate(display(p),(p['value'],i),xytext=(5,0),textcoords='offset points',va='center',fontsize=8)
        fig.set_size_inches(10.5,min(14,max(5,len(rows)*.32+1.5)))
    else:
        ax.bar(range(len(rows)),[p['value'] for p in rows],color=palette[0],width=.6)
        ax.set_xticks(range(len(rows)),[p['label'] for p in rows],rotation=30 if len(rows)>5 else 0);ax.set_ylabel(unit)
        if len(rows)<=20:
            for i,p in enumerate(rows): ax.annotate(display(p),(i,p['value']),xytext=(0,6),textcoords='offset points',ha='center',fontsize=8)
elif kind in ('pie', 'donut'):
    raw_percentages = iter(rows)
    wedges, texts, autotexts = ax.pie([p['value'] for p in rows], labels=[p['label'] for p in rows],
        colors=colors[:len(rows)],autopct=lambda _: display(next(raw_percentages))+'%',startangle=90,counterclock=False,
        wedgeprops={'width':.4,'edgecolor':'white'} if kind=='donut' else {'edgecolor':'white'})
    for t in texts+autotexts: t.set_fontsize(9)
    ax.set_aspect('equal')
elif kind in ('grouped_bar', 'stacked_bar', 'heatmap', 'radar'):
    lookup = {(p.get('series',''),p['label']):p for p in rows}
    values = np.array([[lookup[(s,label)]['value'] for label in labels] for s in series])
    if kind in ('grouped_bar','stacked_bar'):
        bottom = np.zeros(len(labels)); width=.8/len(series)
        for i,s in enumerate(series):
            if kind=='stacked_bar':
                ax.bar(range(len(labels)),values[i],bottom=bottom,label=s,color=colors[i],width=.6);bottom+=values[i]
            else:
                ax.bar(np.arange(len(labels))-.4+width/2+i*width,values[i],label=s,color=colors[i],width=width)
        ax.set_xticks(range(len(labels)),labels,rotation=25 if len(labels)>5 else 0)
        ax.set_ylabel(unit); ax.legend(frameon=False)
    elif kind=='heatmap':
        im=ax.imshow(values,cmap='YlGnBu',aspect='auto')
        ax.set_xticks(range(len(labels)),labels,rotation=30,ha='right');ax.set_yticks(range(len(series)),series)
        fig.colorbar(im,ax=ax,label=unit,shrink=.8)
        if len(rows)<=60:
            for i,s in enumerate(series):
                for j,label in enumerate(labels):
                    ax.text(j,i,display(lookup[(s,label)]),ha='center',va='center',fontsize=8,
                            color='white' if values[i,j]>(values.max()+values.min())/2 else '#23374D')
    else:
        angles=np.linspace(0,2*np.pi,len(labels),endpoint=False).tolist();closed=angles+angles[:1]
        for i,s in enumerate(series):
            vals=values[i].tolist();ax.plot(closed,vals+vals[:1],label=s or rows[0]['metric'],color=colors[i],linewidth=2)
            ax.fill(closed,vals+vals[:1],color=colors[i],alpha=.08)
        ax.set_xticks(angles,labels);ax.set_ylim(0,max(1,float(values.max())*1.15))
        if len(series)>1:ax.legend(loc='upper right',bbox_to_anchor=(1.3,1.1),frameon=False)
        ax.set_ylabel(unit)
elif kind=='scatter':
    lookup={(p['series'],p['label']):p for p in rows}
    xs=[lookup[('x',label)]['value'] for label in labels];ys=[lookup[('y',label)]['value'] for label in labels]
    ax.scatter(xs,ys,s=65,color=palette[0],alpha=.8)
    for label,x,y in zip(labels,xs,ys):ax.annotate(label,(x,y),xytext=(5,5),textcoords='offset points',fontsize=9)
    ax.set_xlabel(lookup[('x',labels[0])]['metric']+' / '+lookup[('x',labels[0])]['unit'])
    ax.set_ylabel(lookup[('y',labels[0])]['metric']+' / '+lookup[('y',labels[0])]['unit'])
ax.set_title(spec.get('title',rows[0]['metric']),fontsize=14,pad=18)
if kind not in ('pie','donut','heatmap','radar'):
    ax.margins(x=.15 if kind=='horizontal_bar' else .06,y=.18)
    ax.grid(axis='x' if kind=='horizontal_bar' else 'y',alpha=.18)
    ax.set_axisbelow(True);ax.spines[['top','right']].set_visible(False)
value_kind={'actual':'实际值','forecast':'预测值','target':'目标值'}[rows[0]['value_kind']]
footer=rows[0]['scope']+' · '+rows[0]['period_basis']+' · '+value_kind+'；来源及原句见数据明细'
if spec.get('display_note'):footer+='\n'+spec['display_note']
fig.text(.02,.015,footer,fontsize=8,color='#52606D')
fig.tight_layout(rect=[0,.065,1,1])
print(json.dumps({'type':kind,'points':len(rows),'series':len(series),'unit':unit},ensure_ascii=False))
'''
