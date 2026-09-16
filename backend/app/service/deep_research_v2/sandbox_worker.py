"""只在容器内运行的执行器。隔离依靠容器权限，不依靠 Python 黑名单。"""
import base64
import contextlib
import importlib.util
import io
import json
import sys
from functools import wraps


class LimitedText(io.StringIO):
    def write(self, text):
        if self.tell() + len(text) > 32768:
            raise RuntimeError('输出超过 32 KB 限制')
        return super().write(text)


request = json.load(sys.stdin)
stdout, stderr = LimitedText(), LimitedText()
result = {'success': False, 'output': '', 'error': None, 'charts': []}
plt = None
try:
    namespace = {'__name__': '__main__'}
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        for module, alias in [('math', 'math'), ('statistics', 'statistics'), ('datetime', 'datetime'),
                              ('json', 'json'), ('collections', 'collections'), ('re', 're'),
                              ('numpy', 'np'), ('pandas', 'pd'), ('seaborn', 'sns')]:
            if importlib.util.find_spec(module):
                namespace[alias] = __import__(module)
                namespace[module] = namespace[alias]
        if importlib.util.find_spec('matplotlib'):
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            plt.rcParams.update({'figure.figsize': [12, 7], 'figure.dpi': 100,
                                 'font.sans-serif': ['Noto Sans CJK JP', 'DejaVu Sans'],
                                 'axes.unicode_minus': False})
            namespace.update(plt=plt, matplotlib=matplotlib)
            # Seaborn 的主题会覆盖 rcParams；主题切换后仍保留中文字体。
            def preserve_chinese_font(function):
                @wraps(function)
                def wrapped(*args, **kwargs):
                    value = function(*args, **kwargs)
                    plt.rcParams.update({'font.family': ['sans-serif'],
                        'font.sans-serif': ['Noto Sans CJK JP', 'DejaVu Sans'], 'axes.unicode_minus': False})
                    return value
                return wrapped
            if 'sns' in namespace:
                for name in ('set_theme', 'set', 'set_style'):
                    setattr(namespace['sns'], name, preserve_chinese_font(getattr(namespace['sns'], name)))
        exec(compile(request['code'], '<research>', 'exec'), namespace)
        if plt is not None:
            figures = plt.get_fignums()
            if len(figures) > 4:
                raise ValueError('单次最多生成 4 张图表')
            for number in figures:
                fig = plt.figure(number)
                if fig.get_axes():
                    from matplotlib.text import Text
                    for label in fig.findobj(match=Text):
                        label.set_fontfamily(['Noto Sans CJK JP', 'DejaVu Sans'])
                    buf = io.BytesIO()
                    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight', facecolor='white')
                    if buf.tell() > 1024 * 1024:
                        raise ValueError('单张 PNG 超过 1 MB 限制')
                    result['charts'].append(base64.b64encode(buf.getvalue()).decode('ascii'))
        result['success'] = True
        result['error'] = stderr.getvalue() or None
except BaseException as exc:
    result.update(success=False, error=f'{type(exc).__name__}: {exc}', charts=[])
finally:
    if plt is not None:
        plt.close('all')
result['output'] = stdout.getvalue()
sys.stdout.write(json.dumps(result, ensure_ascii=False))
