"""Export palettes are fixed data; arbitrary theme CSS is never executed."""
PALETTES = {
    'ocean-blue': ('#edf5fa','#ffffff','#183a50','#46667a','#e6f1f8','#a6c5d9','#086b9c'),
    'light': ('#f6f7f9','#ffffff','#1f2328','#57606a','#eaeef2','#d0d7de','#0969da'),
    'dark': ('#010409','#0d1117','#e6edf3','#b1bac4','#21262d','#57606a','#79c0ff'),
    'sepia': ('#eee5d2','#faf4e6','#463b2d','#6b5943','#eae0cd','#b5a58b','#80532a'),
    'paper-moments': ('#f4ede0','#fffdf4','#514638','#79654f','#eee7d8','#b8a58f','#8c503b'),
    'midnight-purple': ('#100c18','#191322','#eee7f8','#c0accf','#30253f','#705a85','#d3a7ff'),
}

def html_theme(theme_id, warnings):
    if theme_id not in PALETTES:
        warnings.append(f'HTML 不支持主题 {theme_id}，已使用 light 导出配色')
        theme_id = 'light'
    names = ('page','surface','text','muted','code','border','accent')
    return theme_id, ':root{' + ';'.join(f'--{k}:{v}' for k,v in zip(names,PALETTES[theme_id])) + '}'

def print_theme_warning(options, warnings, format_name):
    if options.theme_id != 'light':
        warnings.append(f'{format_name} 使用浅色打印样式，不支持主题 {options.theme_id}；需要主题配色请导出 HTML')

# Semantic type, portable title symbol and contrasting print color.
CALLOUTS = {
    'note': ('i','#0969da'), 'abstract': ('=','#7041a0'),
    'info': ('i','#0969da'), 'todo': ('[ ]','#0969da'),
    'tip': ('+','#176f41'), 'success': ('+','#176f41'),
    'question': ('?','#805400'), 'warning': ('!','#805400'),
    'failure': ('x','#b42318'), 'danger': ('!','#b42318'),
    'bug': ('!','#b42318'), 'important': ('!','#7041a0'), 'example': ('*','#7041a0'), 'quote': ('>','#57606a'),
}
ALIASES = {'summary':'abstract','tldr':'abstract','hint':'tip',
           'check':'success','done':'success','help':'question','faq':'question',
           'caution':'warning','attention':'warning','fail':'failure','missing':'failure',
           'error':'danger','cite':'quote'}
