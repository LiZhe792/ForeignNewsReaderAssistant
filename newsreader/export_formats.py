"""Portable export serializers. User text is never treated as HTML or formulas."""
import csv
import hashlib
import html
import io
import json
import math
import os
import re
import threading
import unicodedata
import zipfile
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

WORD_COLUMNS = [('word', '单词'), ('phonetic', '音标'), ('part_of_speech', '词性'), ('meaning', '释义'),
                ('mastery_label', '掌握度'), ('context', '原文语境'), ('note', '学习笔记'),
                ('article_title', '来源文章'), ('source_url', '来源链接'), ('created_at', '创建时间')]
NOTE_COLUMNS = [('label', '类型'), ('title', '标题 / 卡片正面'), ('content', '正文 / 卡片答案'),
                ('example', '例句与补充'), ('tags', '标签'), ('quote', '原文摘录'), ('article_title', '来源文章'),
                ('source_url', '来源链接'), ('source_state_label', '原文状态'), ('paragraphs', '原文段落'),
                ('created_at', '创建时间'), ('updated_at', '更新时间')]
LIST_COLUMNS = [('title', '文章标题'), ('source', '来源'), ('source_url', '来源链接'), ('category', '分类'),
                ('level', '参考难度'), ('word_count', '词数'), ('reading_minutes', '预计阅读分钟'), ('subtitle', '简介')]
METRICS = [('reading_seconds', '区间阅读时长', '秒'), ('checked_days', '区间打卡天数', '天'),
           ('completed_count', '区间阅读完成次数', '次'), ('longest_streak', '区间最长连续打卡', '天'),
           ('added_vocabulary', '区间新增且仍保留的生词', '个'), ('added_notes', '区间新增且仍保留的笔记', '条'),
           ('current_vocabulary', '导出时生词总数', '个')]
DAILY = [('date', '日期'), ('reading_minutes', '阅读分钟'), ('completed_count', '完成次数'),
         ('checked_in', '已打卡'), ('manual', '手动打卡')]
SOURCE_STATES = {'available': '原文可定位', 'deleted': '原文已删除，摘录保留', 'changed': '原文已变化，摘录保留', 'none': '独立笔记'}
_PDF_LOCK = threading.RLock()
_PDF_FONTS = None


def safe_url(value):
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value) or '\\' in value:
        return ''
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            return ''
        return value
    except ValueError:
        return ''


def scalar(value):
    if isinstance(value, list):
        return ', '.join(str(v) for v in value)
    if isinstance(value, bool):
        return '是' if value else '否'
    return '' if value is None else value


def display_rows(data):
    dataset = data['dataset']
    if dataset == 'report':
        report = data['report']
        columns = [('type', '记录类型'), ('date', '日期'), ('label', '统计项'), ('value', '数值'), ('unit', '单位')]
        rows = [{'type': '区间概要', 'label': label, 'value': report['metrics'][key], 'unit': unit} for key, label, unit in METRICS]
        rows += [{'type': '导出时掌握分布', 'label': label, 'value': report['mastery'][key], 'unit': '个'}
                 for key, label in [('new', '初次遇见'), ('learning', '正在学习'), ('mastered', '已掌握')]]
        for day in report['daily']:
            for key, label, unit in [('reading_minutes', '阅读时长', '分钟'), ('completed_count', '阅读完成次数', '次'), ('checked_in', '打卡', '1=是 / 0=否')]:
                rows.append({'type': '每日记录', 'date': day['date'], 'label': label, 'value': int(day[key]) if isinstance(day[key], bool) else day[key], 'unit': unit})
        return columns, rows
    columns = {'vocabulary': WORD_COLUMNS, 'notes': NOTE_COLUMNS, 'reading_list': LIST_COLUMNS}[dataset]
    rows = []
    for item in data['items']:
        value = dict(item)
        if dataset == 'notes':
            value['source_state_label'] = SOURCE_STATES[item['source_state']]
            value['paragraphs'] = ', '.join(str(a['paragraph']+1) for a in item['anchors'])
        rows.append(value)
    return columns, rows


def csv_cell(value):
    value = scalar(value)
    if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')):
        return "'"+value  # Excel/LibreOffice must not execute imported user text.
    return value


def csv_bytes(columns, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, quoting=csv.QUOTE_MINIMAL, lineterminator='\r\n')
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([csv_cell(row.get(key)) for key, _ in columns])
    return stream.getvalue().encode('utf-8-sig')


def md_text(value):
    value = html.escape(str(value or ''), quote=False).replace('\\', '\\\\')
    return re.sub(r'([`*_{}\[\]<>#+!|~])', r'\\\1', value)


def link(label, url):
    return '['+md_text(label)+']('+quote(url, safe=':/?&=%#@+;,')+')' if safe_url(url) else md_text(label)


def record_md(item, dataset):
    lines = ['# '+md_text(item.get('word', item.get('title', ''))), '']
    if dataset == 'vocabulary':
        labels = [('phonetic', '音标'), ('part_of_speech', '词性'), ('meaning', '释义'), ('mastery_label', '掌握度'), ('context', '原文语境'), ('note', '学习笔记')]
    else:
        labels = [('label', '类型'), ('content', '正文 / 答案'), ('example', '例句与补充'), ('tags', '标签'), ('quote', '原文摘录')]
    for key, title in labels:
        if item.get(key):
            lines += ['## '+title, '', md_text(scalar(item[key])), '']
    if item.get('article_title'):
        lines += ['来源：'+link(item['article_title'], item.get('source_url', '')), '']
    if item.get('anchors'):
        lines += ['原文段落：'+', '.join(str(a['paragraph']+1) for a in item['anchors']), '', SOURCE_STATES[item['source_state']], '']
    return '\n'.join(lines)


def markdown(data):
    lines = ['# Margin 知刊 · '+data['title'], '', '导出时间：'+data['generated_at'], '']
    if data['dataset'] == 'report':
        r = data['report']
        lines += ['统计区间：'+r['period']['from']+' 至 '+r['period']['to']+'（北京时间）', '', r['definitions'], '', '## 区间概要', '']
        lines += [f"- {label}：{r['metrics'][key]} {unit}" for key, label, unit in METRICS]
        lines += ['', '## 导出时掌握分布', '', f"初次遇见 {r['mastery']['new']} 个，正在学习 {r['mastery']['learning']} 个，已掌握 {r['mastery']['mastered']} 个。", '', '## 每日记录', '', '| 日期 | 阅读分钟 | 完成次数 | 打卡 |', '| --- | ---: | ---: | --- |']
        lines += [f"| {d['date']} | {d['reading_minutes']} | {d['completed_count']} | {'是' if d['checked_in'] else '否'} |" for d in r['daily']]
    elif data['dataset'] == 'reading_list':
        lines += ['文章信息与公开来源链接。无来源链接的个人导入文章需要由分享者另外提供。', '']
        for item in data['items']:
            lines += ['## '+link(item['title'], item['source_url']), '', md_text(item['source'])+' · '+md_text(item['category'])+' · '+md_text(item['level'])+f" · {item['word_count']} 词 · 约 {item['reading_minutes']} 分钟", '', md_text(item['subtitle']), '']
    else:
        for item in data['items']:
            lines += ['---', '', record_md(item, data['dataset']), '']
    return '\n'.join(lines).encode('utf-8')


def spreadsheet(data):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    workbook = Workbook()
    workbook.remove(workbook.active)

    def sheet(name, columns, rows, note):
        ws = workbook.create_sheet(name)
        ws.sheet_view.showGridLines = False
        ws.append(['Margin 知刊 · '+name])
        ws.append([note])
        ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(columns))
        ws.append([label for _, label in columns])
        for row in rows:
            values = []
            for key, _ in columns:
                value = scalar(row.get(key))
                if key in ('created_at', 'updated_at') and value:
                    value = datetime.fromisoformat(value).replace(tzinfo=None)
                elif key == 'date' and value:
                    value = date.fromisoformat(value)
                if isinstance(value, str):
                    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', value)
                    if len(value) > 32767:
                        raise ValueError('某个字段超过 Excel 单元格长度上限，请使用 JSON 或 Markdown 导出。')
                values.append(value)
            ws.append(values)
        for row in ws:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = 's'
                cell.font = Font(name='Microsoft YaHei', size=11, color='293C30')
                cell.alignment = Alignment(vertical='top', wrap_text=True)
                if isinstance(cell.value, datetime):
                    cell.number_format = 'yyyy-mm-dd hh:mm'
                elif isinstance(cell.value, date):
                    cell.number_format = 'yyyy-mm-dd'
        ws['A1'].font = Font(name='Microsoft YaHei', size=18, bold=True, color='254B3E')
        ws['A2'].font = Font(name='Microsoft YaHei', size=10, color='637466')
        ws.row_dimensions[1].height = 32
        ws.row_dimensions[2].height = 36
        ws.row_dimensions[3].height = 28
        for cell in ws[3]:
            cell.fill = PatternFill('solid', fgColor='254B3E')
            cell.font = Font(name='Microsoft YaHei', size=11, bold=True, color='FFFFFF')
        for index, (key, _) in enumerate(columns, 1):
            ws.column_dimensions[get_column_letter(index)].width = 46 if key in ('content', 'meaning', 'quote', 'context', 'note', 'example', 'subtitle', 'label', 'source_url') else 26 if key in ('title', 'article_title', 'created_at', 'updated_at') else 18
        for index in range(4, ws.max_row+1):
            line_count = 1
            for cell in ws[index]:
                width = max(8, ws.column_dimensions[cell.column_letter].width-2)
                lines = str(cell.value or '').split('\n')
                line_count = max(line_count, sum(max(1, math.ceil(sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in line)/width)) for line in lines))
            ws.row_dimensions[index].height = min(409, max(28, line_count*17+10))
            if index % 2 == 0:
                for cell in ws[index]:
                    cell.fill = PatternFill('solid', fgColor='F2F6F0')
        ws.freeze_panes = 'A4'
        ws.auto_filter.ref = f'A3:{get_column_letter(len(columns))}{ws.max_row}'
        ws.print_title_rows = '1:3'
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0

    if data['dataset'] == 'report':
        r = data['report']
        columns, rows = display_rows(data)
        sheet('报告概要', [('label', '统计项'), ('value', '数值'), ('unit', '单位')], rows[:10], r['period']['from']+' 至 '+r['period']['to']+'；掌握分布为导出时现状。')
        sheet('每日学习', DAILY, r['daily'], '北京时间；阅读时长按分钟，零值表示当天无对应活动记录。')
    else:
        columns, rows = display_rows(data)
        sheet(data['title'], columns, rows, '导出时间：'+data['generated_at']+'（北京时间）。长文本完整保留，可调整行高或在编辑栏查看。')
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def pdf_fonts():
    global _PDF_FONTS
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    if _PDF_FONTS:
        return _PDF_FONTS
    candidates = [os.environ.get('MARGIN_PDF_FONT', ''), 'C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simsun.ttc',
                  '/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf', '/System/Library/Fonts/STHeiti Light.ttc']
    names = []
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont('MarginText', candidate))
            names.append('MarginText')
            break
        except Exception:
            continue
    if not names:
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        names.append('STSong-Light')
    for index, path in enumerate(['C:/Windows/Fonts/arial.ttf', 'C:/Windows/Fonts/segoeui.ttf',
                                 'C:/Windows/Fonts/seguiemj.ttf', 'C:/Windows/Fonts/simsunb.ttf',
                                 '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']):
        if Path(path).is_file():
            try:
                name = 'MarginFallback'+str(index)
                pdfmetrics.registerFont(TTFont(name, path))
                names.append(name)
            except Exception:
                continue
    _PDF_FONTS = names
    return names


def pdf_bytes(data):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, CondPageBreak
    with _PDF_LOCK:
        fonts = pdf_fonts()
        substituted = set()

        def text(value):
            chunks, run, run_font = [], '', fonts[0]
            for char in str(scalar(value)):
                if ord(char) < 32 and char not in '\n\r\t':
                    continue
                name = next((name for name in fonts if (name == 'STSong-Light' and ord(char) <= 65535) or ord(char) in getattr(pdfmetrics.getFont(name).face, 'charToGlyph', {})), None)
                if char in '\n\r\t':
                    name = fonts[0]
                if name is None:
                    substituted.add(char)
                    char, name = '[U+'+format(ord(char), '04X')+']', fonts[0]
                if name != run_font and run:
                    chunks.append((run_font, run))
                    run = ''
                run_font, run = name, run+char
            if run:
                chunks.append((run_font, run))
            return ''.join('<font name="'+name+'">'+html.escape(value).replace('\n', '<br/>').replace('\r', '').replace('\t', '    ')+'</font>' for name, value in chunks)

        base = ParagraphStyle('MarginBody', fontName=fonts[0], fontSize=10.5, leading=17, spaceAfter=7, textColor=colors.HexColor('#293C30'), wordWrap='CJK', splitLongWords=True, alignment=TA_LEFT)
        heading = ParagraphStyle('MarginHeading', parent=base, fontSize=14, leading=21, spaceBefore=14, spaceAfter=9, keepWithNext=True, textColor=colors.HexColor('#254B3E'))
        title = ParagraphStyle('MarginTitle', parent=heading, fontSize=22, leading=30, spaceBefore=0, spaceAfter=14)
        caption = ParagraphStyle('MarginCaption', parent=base, fontSize=9, leading=14, textColor=colors.HexColor('#637466'))
        story = [Paragraph(text('Margin 知刊 · '+data['title']), title), Paragraph(text('导出时间：'+data['generated_at']), caption), Spacer(1, 9)]
        if data['dataset'] == 'report':
            r = data['report']
            story += [Paragraph(text(r['period']['from']+' 至 '+r['period']['to']+'（北京时间）'), base), Paragraph(text(r['definitions']), caption)]
            columns, rows = display_rows(data)
            for row in rows[:10]:
                story.append(Paragraph(text(row['label']+'：'+str(row['value'])+' '+row['unit']), base))
            story.extend([CondPageBreak(110), Paragraph('每日学习记录', ParagraphStyle('DailyHeading', parent=heading, keepWithNext=False))])
            cells = [[Paragraph(text(label), base) for _, label in DAILY]]
            cells += [[Paragraph(text(d[key]), base) for key, _ in DAILY] for d in r['daily']]
            table = Table(cells, colWidths=[105, 95, 85, 90, 90], repeatRows=1, hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E8EFE4')), ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F6F8F4')]), ('VALIGN', (0, 0), (-1, -1), 'TOP'), ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 4)]))
            story.append(table)
        else:
            columns, rows = display_rows(data)
            for number, item in enumerate(rows, 1):
                record_title = item.get('word', item.get('title', ''))
                story.append(Paragraph(text(str(number)+'. '+record_title), heading))
                for key, label in columns:
                    if key in ('word', 'title') or item.get(key) in ('', None, []):
                        continue
                    story.append(Paragraph(text(label+'：'+str(scalar(item[key]))), base))
        if substituted:
            story.append(Paragraph(text('字体未覆盖的符号以 [U+编号] 表示；JSON 或 Markdown 导出保留原字符。'), caption))
        output = io.BytesIO()
        document = SimpleDocTemplate(output, pagesize=A4, rightMargin=52, leftMargin=52, topMargin=48, bottomMargin=48,
                                     title='Margin - '+data['title'], author='Margin')

        def footer(canvas, doc):
            canvas.saveState()
            canvas.setFont(fonts[0], 9)
            canvas.setFillColor(colors.HexColor('#637466'))
            canvas.drawString(52, 26, 'Margin 知刊')
            canvas.drawRightString(A4[0]-52, 26, str(doc.page))
            canvas.restoreState()
        document.build(story, onFirstPage=footer, onLaterPages=footer)
        return output.getvalue()


def reading_html(data):
    escape = lambda value: html.escape(str(value or ''), quote=True)
    cards = []
    for item in data['items']:
        title = escape(item['title'])
        if item['source_url']:
            title = '<a href="'+escape(item['source_url'])+'" target="_blank" rel="noopener noreferrer">'+title+'</a>'
        cards.append('<article><h2>'+title+'</h2><p class="meta">'+escape(item['source'])+' · '+escape(item['category'])+' · '+escape(item['level'])+f" · {item['word_count']} 词 · 约 {item['reading_minutes']} 分钟</p><p>"+escape(item['subtitle'])+'</p>'+('' if item['source_url'] else '<p class="meta">个人导入文章，无公开来源链接。</p>')+'</article>')
    return ('<!doctype html><html lang="zh-CN"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; style-src &#39;unsafe-inline&#39;"><title>Margin 阅读清单</title><style>body{margin:0;background:#f5f7f1;color:#293c30;font:16px/1.8 system-ui,sans-serif}main{max-width:850px;margin:auto;padding:36px 22px}h1{font-size:30px}h2{font-size:22px;margin:0}article{background:white;border:1px solid #dce4d7;border-radius:12px;padding:24px;margin:20px 0;overflow-wrap:anywhere}a{color:#254b3e}.meta{color:#667464;font-size:14px}@media print{body{background:white}article{break-inside:avoid}}</style><main><p class="meta">MARGIN · READING LIST</p><h1>一起读一点世界</h1><p>共 '+str(data['count'])+' 篇文章。此清单包含文章信息与来源链接。</p>'+''.join(cards)+'</main></html>').encode('utf-8')


def notes_package(data, target):
    output = io.BytesIO()
    dataset = data['dataset']
    prefix = 'words' if dataset == 'vocabulary' else 'notes'
    index = ['# Margin 知刊 · '+data['title'], '', '导出时间：'+data['generated_at'], '']
    source_notes = {}
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for item in data['items']:
            name = prefix+'/'+str(item['id'])+'.md'
            title = item.get('word', item.get('title', ''))
            tags = item.get('tags', ['vocabulary', item.get('mastery', '')])
            frontmatter = '---\ntitle: '+json.dumps(title, ensure_ascii=False)+'\ntags: '+json.dumps(tags, ensure_ascii=False)+'\nmargin_id: '+str(item['id'])+'\n---\n\n' if target == 'obsidian' else ''
            content = frontmatter+record_md(item, dataset)
            index.append('- ['+md_text(title)+']('+name+')')
            if item.get('article_id'):
                article_id = item['article_id']
                source_notes.setdefault(article_id, {'title': item['article_title'], 'url': item['source_url'], 'excerpts': []})
                anchors = item.get('anchors') or ([{'quote': item['context'], 'paragraph': -1}] if item.get('context') else [])
                for anchor_number, anchor in enumerate(anchors, 1):
                    block = f"note-{item['id']}-p-{anchor['paragraph']+1}-{anchor_number}"
                    source_notes[article_id]['excerpts'].append((title, name, anchor['quote'], block))
                    content += f'\n\n[来源摘录](../sources/article-{article_id}.md'+('#^'+block if target == 'obsidian' else '')+')\n'
            archive.writestr(name, content.encode('utf-8'))
        for article_id, source in source_notes.items():
            content = '# '+md_text(source['title'])+'\n\n'+link('公开来源', source['url'])+'\n\n这里只包含本次导出的相关摘录。\n\n'
            for title, name, excerpt, block in source['excerpts']:
                content += md_text(excerpt)+('\n^'+block if target == 'obsidian' else '')+'\n\n[返回：'+md_text(title)+'](../'+name+')\n\n'
            archive.writestr(f'sources/article-{article_id}.md', content.encode('utf-8'))
        archive.writestr('index.md', '\n'.join(index).encode('utf-8'))
        instructions = ('解压后，将本文件夹复制到你的 Obsidian 仓库。打开 index.md；文件间链接、标签和来源摘录已保留。' if target == 'obsidian' else '在 Notion 的导入菜单选择 Markdown 与 CSV，导入这个 ZIP（也可先解压再导入文件）。笔记在 notes/ 或 words/，CSV 可单独导入为数据库。导入后请按 Notion 的提示检查页面链接。')
        archive.writestr('README.txt', (instructions+'\n\n这是一次导出快照。修改不会自动同步回 Margin。重新导入同一包时，请自行选择更新或去重，避免重复页面。').encode('utf-8'))
        if target == 'notion':
            archive.writestr('records.csv', csv_bytes(*display_rows(data)))
    return output.getvalue()


def anki_package(data, deck_name, namespace):
    import genanki
    stable_id = lambda value: int(hashlib.sha256(value.encode()).hexdigest()[:8], 16) & 0x7fffffff
    model = genanki.Model(1876419021, 'Margin Reading Cards v1', fields=[{'name': 'Front'}, {'name': 'Back'}, {'name': 'Source'}],
        templates=[{'name': 'Recall', 'qfmt': '{{Front}}', 'afmt': '{{FrontSide}}<hr id="answer">{{Back}}<div class="source">{{Source}}</div>'}],
        css='.card{font-family:Arial,"Microsoft YaHei",sans-serif;font-size:21px;line-height:1.7;text-align:left;color:#293c30;background:#fff}.source{font-size:14px;color:#637466;margin-top:20px}.nightMode.card{color:#e2eade;background:#17221d}.nightMode .source{color:#afc0b1}')
    deck = genanki.Deck(stable_id(namespace+':'+data['dataset']+':'+deck_name), deck_name)
    clean = lambda value: html.escape(re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', str(value or '')), quote=True).replace('\n', '<br>')
    for item in data['items']:
        if data['dataset'] == 'vocabulary':
            front = item['word']
            back = '\n'.join(filter(None, [item['phonetic'], item['part_of_speech'], item['meaning'], item['context'], item['note']]))
            tags = ['margin', 'vocabulary', item['mastery']]
        else:
            if item['kind'] != 'card':
                raise ValueError('Anki 笔记导出仅支持知识卡片，请先把笔记制成卡片。')
            front = item['title']
            back = '\n\n'.join(filter(None, [item['content'], item['example'], item['quote']]))
            tags = ['margin', 'knowledge', *item['tags']]
        source = clean(item.get('article_title', ''))
        if item.get('source_url'):
            source += '<br><a href="'+html.escape(item['source_url'], quote=True)+'">原文链接</a>'
        guid = genanki.guid_for(namespace, data['dataset'], item['id'])
        deck.add_note(genanki.Note(model=model, fields=[clean(front), clean(back), source], guid=guid,
                                  tags=[re.sub(r'[\s\x00-\x1f]+', '_', tag) for tag in tags if tag]))
    output = io.BytesIO()
    genanki.Package(deck).write_to_file(output)
    return output.getvalue()


def render_export(data, fmt, *, deck_name='', namespace=''):
    if fmt == 'csv':
        return csv_bytes(*display_rows(data)), 'csv', 'text/csv; charset=utf-8'
    if fmt == 'xlsx':
        return spreadsheet(data), 'xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    if fmt == 'json':
        return json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'), 'json', 'application/json'
    if fmt == 'md':
        return markdown(data), 'md', 'text/markdown; charset=utf-8'
    if fmt == 'pdf':
        return pdf_bytes(data), 'pdf', 'application/pdf'
    if fmt == 'html':
        return reading_html(data), 'html', 'text/html; charset=utf-8'
    if fmt in ('obsidian', 'notion'):
        return notes_package(data, fmt), 'zip', 'application/zip'
    if fmt == 'anki':
        return anki_package(data, deck_name, namespace), 'apkg', 'application/octet-stream'
    raise ValueError('不支持的导出格式。')
