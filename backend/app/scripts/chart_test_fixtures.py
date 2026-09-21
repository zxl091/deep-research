"""明确标记为虚构的图表验收资料，不写入业务数据库。"""
import copy


def chart_fixtures():
    sources = {}
    def point(label, value, *, period='2024', unit='亿元', metric='营收', series='', kind='actual'):
        entity = '样本公司' if str(label).isdigit() else (label if series in ('x', 'y') else series + label)
        quote=f'虚构测试：{period}年全年，{entity}的{metric}为{value}{unit}。'
        url=f'https://fixture.test/{len(sources)}'
        sources[url]={'url':url,'title':'虚构测试数据','content':quote}
        return dict(label=label,entity=entity,value=value,period=period,unit=unit,metric=metric,series=series,
                    scope='虚构企业样本',period_basis='全年',value_kind=kind,source_url=url,quote=quote)
    specs=[]
    def add(kind,rows):specs.append({'title':'虚构验收 · '+kind,'type':kind,'points':rows})
    add('line',[point(str(y),n,period=str(y)) for y,n in [(2020,20),(2021,25),(2025,50)]])
    ranked=[point(label,n) for label,n in [('甲',20),('乙',30),('丙',50)]]
    add('bar',ranked);add('horizontal_bar',copy.deepcopy(ranked))
    shares=[point(label,n,unit='%',metric='份额') for label,n in [('甲',20),('乙',30),('丙',50)]]
    add('pie',shares);add('donut',copy.deepcopy(shares))
    matrix=[point(label,n+i*10,series=series) for i,series in enumerate(['样本一','样本二']) for label,n in [('甲',20),('乙',30),('丙',50)]]
    add('grouped_bar',matrix);add('heatmap',copy.deepcopy(matrix));add('radar',copy.deepcopy(matrix))
    add('stacked_bar',[point(label,value,series=series,unit='%',metric='收入构成') for label in ['甲','乙','丙'] for series,value in [('业务一',40),('业务二',60)]])
    add('scatter',[point(label,value,series=axis,unit=unit,metric=metric) for label,n in [('甲',1),('乙',2),('丙',3)] for axis,value,unit,metric in [('x',n*10,'人','员工数'),('y',n*4,'亿元','营收')]])
    return specs,sources
