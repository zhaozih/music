#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local Windows/Linux pre-publication export of Douyin Collector V5.8.10 works.

Only allowlisted columns are exported. This cannot guarantee anonymity of prose or video.
V1.2: a missing or ambiguous video no longer aborts unrelated works; summary report added.
DO NOT upload original collector folders or the originals of CSV/JSON/ZIP.
"""
import argparse
import csv
import json
import re
import unicodedata
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

VIDEO_EXT = {'.mp4'}
EMAIL = re.compile(r'(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}(?![A-Za-z0-9.\-])', re.I)
PHONE = re.compile(r'(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d[\s-]?\d{4}[\s-]?\d{4}(?!\d)')
URL = re.compile(r'(?:https?://|www\.)[^\s，。；;]+', re.I)
AT = re.compile(r'@[\w\u4e00-\u9fff.\-]{1,32}')
WX = re.compile(r'(?:微信|vx|VX|V信|wx)\s*(?:号|[:：是加])\s*[a-zA-Z][-_a-zA-Z0-9]{5,}',re.I)
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def text_file(path):
    for encoding in ('utf-8-sig','utf-8','gb18030'):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeError:
            pass
    raise ValueError(f'Unable to decode {path}')


def read_csv(path):
    content = text_file(path)
    return list(csv.DictReader(content.splitlines()))


def field(row, keys, default=''):
    mapped = {str(k).strip().lower().replace(' ', ''):v for k,v in row.items() if k}
    for key in keys:
        value=mapped.get(key.lower())
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def count(value):
    if value is None: return ''
    value=str(value).strip()
    if not value or value in ('-1','None','null'): return ''
    if value.isdecimal(): return value
    # Support only unambiguous simple numeric values
    return ''


def clean_name(s, fallback):
    s = BAD.sub('_', str(s or '')).strip().strip('. ')
    if not s: s=fallback
    return s[:72].strip().rstrip('.') or fallback


def _compatible_normalize(s):
    """Create a same-length detection view, retaining indices into the original text.

    Unicode compatibility folding can expand a character to multiple codepoints;
    preserve the original character when it would change offset lengths.
    """
    return ''.join(n if len(n := unicodedata.normalize('NFKC', c)) == 1 else c for c in s)


def redact(s):
    """Best-effort masking; NOT a guarantee of anonymity.

    Original narrative text is kept where possible. Ambiguous @mentions may
    mask adjacent characters; mark such comments for human semantic review.
    """
    s = str(s or '')
    flags=[]
    for kind, patt, replacement in [('url',URL,'[链接]'),('email',EMAIL,'[邮箱]'),('phone',PHONE,'[手机号]'),('wechat',WX,'[联系方式]'),('mention',AT,'[提及账号]')]:
        scan = _compatible_normalize(s)
        spans = [m.span() for m in patt.finditer(scan)]
        for start, end in reversed(spans):
            s=s[:start]+replacement+s[end:]
        if spans: flags.append(kind)
    # Fictional character names, real names, locations, identity clues and other
    # indirect personal information are NOT removed automatically.
    return s,flags


def read_json(path):
    if not path.exists(): return {}
    try: return json.loads(text_file(path))
    except (json.JSONDecodeError,UnicodeError): return {}


def collect_comments(work):
    raw=work/'原始数据'
    files=sorted(p for p in raw.rglob('*.csv') if '评论' in p.stem or 'comment' in p.stem.lower()) if raw.exists() else []
    if not files:
        a=work/'分析数据'
        if a.exists():
            files=sorted(p for p in a.glob('AI_评论点赞排序*.csv'))
    comments={}
    flagged=0
    for f in files:
        for row in read_csv(f):
            body=field(row,('text','content','评论内容','评论文本'))
            if not body:continue
            red,flags=redact(body)
            if flags:flagged+=1
            raw_cid=field(row,('cid','comment_id','评论id'))
            ct=field(row,('create_time','create_timestamp','评论时间','发布时间'))
            key=('cid',raw_cid) if raw_cid else ('text',body,ct)
            item={
                'text':red,
                'comment_likes':count(field(row,('digg_count','comment_likes','like_count','likes','点赞数'))),
                'reply_count':count(field(row,('reply_comment_total','replies_reported','reply_count','回复数'))),
                'comment_date':ct[:10] if ct else '',
                'redaction_flags':';'.join(flags),
            }
            if key not in comments:
                comments[key]=item
            else:
                prior=comments[key]
                for m in ('comment_likes','reply_count'):
                    if item[m] and (not prior[m] or int(item[m])>int(prior[m])):
                        prior[m]=item[m]
    rows=list(comments.values())
    rows.sort(key=lambda r: (-(int(r['comment_likes'] or 0)), -(int(r['reply_count'] or 0)),r['text']))
    for i,row in enumerate(rows,1):row['sample_id']=f'C{i:06d}'
    return rows,flagged,files


def choose_destination(outroot, account, title, work_id):
    """Reuse the same work-ID destination; disambiguate duplicate titles."""
    base = outroot/account
    base.mkdir(parents=True, exist_ok=True)
    preferred = base/title
    candidates = [preferred, base/clean_name(f'{title[:56]}__{work_id[-10:]}', '作品_'+work_id)]
    for candidate in candidates:
        existing = read_json(candidate/'作品信息_公开版.json') if candidate.exists() else {}
        if not candidate.exists() or str(existing.get('work_id', '')) == work_id:
            return candidate
    for i in range(2, 1000):
        candidate=base/clean_name(f'{title[:48]}__{work_id[-10:]}_{i}','作品_'+work_id)
        existing=read_json(candidate/'作品信息_公开版.json') if candidate.exists() else {}
        if not candidate.exists() or str(existing.get('work_id', '')) == work_id:
            return candidate
    raise ValueError('Too many output title collisions')


def video_candidates(work):
    vdir = work/'视频'
    if not vdir.exists():
        return [], '源作品没有“视频”文件夹'
    videos=sorted(p for p in vdir.iterdir()
                  if p.is_file() and not p.is_symlink() and p.suffix.lower() in VIDEO_EXT)
    if not videos:
        return [], '视频文件夹中没有 MP4：可能此前采集未成功下载，或视频已经放到 GitHub'
    if len(videos)>1:
        return [], f'视频文件夹有 {len(videos)} 个 MP4，无法确认对应关系，跳过视频复制'
    return videos, ''


def export_work(work,outroot,include_video=False):
    """Export public data even if there is no MP4. Return machine-readable outcome."""
    info=read_json(work/'作品信息.json')
    work_id=str(info.get('作品ID') or '').strip()
    if not work_id or not work_id.isdecimal():
        raise ValueError('作品信息.json 缺少有效作品ID（未导出此作品）')
    account=clean_name(work.parent.name,'账号待识别')
    title=clean_name(work.name,'未识别作品')
    dest=choose_destination(outroot,account,title,work_id)
    dest.mkdir(parents=True,exist_ok=True)
    account_author=info.get('作者昵称') or work.parent.name
    safe_info={
        'schema_version':'public_reference_v1',
        'work_id':work_id,
        'work_title':str(info.get('作品标题') or title),
        'author_display_name':str(account_author),
        'source_url':str(info.get('作品链接') or ''),
        'collection_timestamp_utc':str(info.get('最近采集UTC') or ''),
        'metrics_as_reported':{k: (info.get('最近作品指标') or {}).get(k) for k in ('views','likes','comments','favorites','shares')},
        'video_analysis_status':'not_yet_analyzed',
        'nested_reply_text_collected':False,
        'comment_sample_is_complete':False,
    }
    (dest/'作品信息_公开版.json').write_text(json.dumps(safe_info,ensure_ascii=False,indent=2),encoding='utf-8')
    sub=dest/'分析数据_公开版'
    sub.mkdir(exist_ok=True)
    rows, flagged, files=collect_comments(work)
    cols=['sample_id','text','comment_likes','reply_count','comment_date','redaction_flags']
    with (sub/'评论_匿名化.csv').open('w',encoding='utf-8-sig',newline='') as fp:
        writer=csv.DictWriter(fp,fieldnames=cols)
        writer.writeheader();writer.writerows(rows)
    stats={
        'sample_count':len(rows),
        'comments_with_likes':sum(c['comment_likes']!='' for c in rows),
        'comments_with_replies':sum(c['reply_count']!='' for c in rows),
        'top_comment_likes':max([int(c['comment_likes']) for c in rows if c['comment_likes']!=''],default=None),
        'top_comment_replies':max([int(c['reply_count']) for c in rows if c['reply_count']!=''],default=None),
        'metrics_as_reported':safe_info['metrics_as_reported'],
        'caution':'点赞高不等于认同；回复多不等于争议；评论覆盖范围未知',
    }
    (sub/'互动数据_公开版.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    problems=[]
    if not files:
        problems.append('找不到该作品的原始评论 CSV 或点赞榜 CSV；导出空评论表，不能理解为作品没有评论')
    elif not rows:
        problems.append('找到了评论数据文件但未提取到可分析的评论正文；需检查原始采集结果')
    copied=0
    if include_video:
        videos,warning=video_candidates(work)
        if warning:
            problems.append(warning)
        else:
            target=dest/'视频'
            target.mkdir(exist_ok=True)
            shutil.copy2(videos[0],target/videos[0].name)
            copied=1
    report={
        'generated_utc':datetime.now(timezone.utc).isoformat(),
        'origin':'local Douyin Collector raw evidence',
        'policy':'ALLOWLIST_ONLY: no raw CSV, no CID, no UID, no sec_uid, no commenter nickname, no IP label, no Cookie',
        'comment_count':len(rows),'redaction_triggered_rows':flagged,
        'comment_sources_count':len(files),'videos_copied':copied,
        'video_requested':bool(include_video),
        'video_copy_status':('copied' if copied else ('not_requested' if not include_video else 'missing_or_ambiguous')),
        'export_status':'PARTIAL' if problems else 'OK',
        'warnings':problems,
        'manual_review_required':True,
        'warning':'Text and audio/video can still contain identifying data; public sharing must be reviewed manually. Masks can harm semantics, especially mentions.',
    }
    (sub/'脱敏检查报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (dest/'README_上传说明.txt').write_text('本目录为白名单公开导出：含作品基本信息、评论文本（初步替换手机号/邮箱/链接等）、点赞回复数。\n'
          '评论ID及评论者账号字段不导出；仍需人工检查正文和视频是否包含个人信息，版权及授权须自行确认。\n'
          '不要上传本工具外的 原始数据/、采集历史/、账号信息.json、原始数据证据ZIP或settings.json。\n'
          '缺少视频并不会停止评论脱敏，详见分析数据_公开版/脱敏检查报告.json。\n',encoding='utf-8')
    return {
        'work_id':work_id,
        'account':account,
        'work_folder':dest.relative_to(outroot).as_posix(),
        'status':'PARTIAL' if problems else 'OK',
        'comments_exported':len(rows),
        'comment_sources':len(files),
        'video_copied':copied,
        'warnings':problems,
    }


def discover_works(src):
    """Find all archived work records, excluding historic snapshots and job artifacts."""
    ignored={'采集历史','_原始批次备份','_批量任务','_处理中'}
    if (src/'作品信息.json').is_file():
        return [src]
    works=[]
    for info in src.rglob('作品信息.json'):
        if info.is_symlink() or any(part in ignored for part in info.relative_to(src).parts):
            continue
        works.append(info.parent)
    return sorted(set(works),key=lambda p:p.as_posix())


def write_batch_report(out, items, source_count, include_video):
    folder=out/'_导出报告'
    folder.mkdir(parents=True,exist_ok=True)
    statuses={k:sum(row['status']==k for row in items) for k in ('OK','PARTIAL','FAILED')}
    summary={
        'version':'public_reference_export_v1.2',
        'generated_utc':datetime.now(timezone.utc).isoformat(),
        'works_discovered':source_count,
        'works_processed':len(items),
        'ok':statuses['OK'],
        'partial':statuses['PARTIAL'],
        'failed':statuses['FAILED'],
        'comments_exported_total':sum(row['comments_exported'] for row in items),
        'videos_copied_total':sum(row['video_copied'] for row in items),
        'video_copy_requested':include_video,
        'note':'只代表成功发现的作品目录和已抓取评论，不代表账号全量作品或全量评论。PARTIAL 也生成了数据。',
    }
    (folder/'导出汇总.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    fields=['status','account','work_folder','work_id','comments_exported','comment_sources','video_copied','warnings']
    with (folder/'逐作品导出清单.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        for item in items:
            line=dict(item)
            line['warnings']='；'.join(item['warnings'])
            writer.writerow(line)
    lines=[
        '导出汇总（不代表抖音平台全量数据）',
        f'识别作品 {source_count}，正常 {statuses["OK"]}，部分成功 {statuses["PARTIAL"]}，失败 {statuses["FAILED"]}',
        f'导出评论 {summary["comments_exported_total"]} 条，复制视频 {summary["videos_copied_total"]} 个',
        '',
    ]
    for item in items:
        if item['status']!='OK':
            lines.append(f'[{item["status"]}] {item["work_folder"]} | '+'；'.join(item['warnings']))
    lines.append('')
    lines.append('注意：公开上传前仍需人工复查评论文字、作品标题、作者信息和视频内容。')
    (folder/'导出结果说明.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return summary


def main():
    p=argparse.ArgumentParser(description='V1.2: batch and privacy-minimized Douyin reference export')
    p.add_argument('--source',required=True,help='采集成果 / 一个账号目录 / 一个作品目录')
    p.add_argument('--output',required=True,help='独立的公开版导出目录')
    p.add_argument('--include-video',action='store_true',help='尽量复制每条作品的视频；缺失时仍继续脱敏所有作品')
    args=p.parse_args()
    src=Path(args.source).expanduser().resolve()
    out=Path(args.output).expanduser().resolve()
    if not src.exists():p.error('Source does not exist')
    if src==out or src in out.parents or out in src.parents:
        p.error('Source and output must be separate independent folders')
    works=discover_works(src)
    if not works:p.error('No 作品信息.json in source: there may be no archived works')
    items=[]
    for i,w in enumerate(works,1):
        rel=w.relative_to(src).as_posix() or w.name
        print(f'[{i}/{len(works)}] 处理 {rel}',flush=True)
        try:
            item=export_work(w,out,args.include_video)
        except Exception as e:
            item={
                'status':'FAILED',
                'account':w.parent.name,
                'work_folder':rel,
                'work_id':'',
                'comments_exported':0,
                'comment_sources':0,
                'video_copied':0,
                'warnings':[f'{type(e).__name__}: {e}'],
            }
        items.append(item)
        print(f'  {item["status"]}: {item["work_folder"]} | 评论 {item["comments_exported"]} | 视频 {item["video_copied"]}',flush=True)
        for warning in item['warnings']:
            print('  提示：'+warning,flush=True)
    summary=write_batch_report(out,items,len(works),args.include_video)
    print('\n完成：',summary['works_discovered'],'部作品，',summary['ok'],'成功，',summary['partial'],'部分成功，',summary['failed'],'失败。')
    print('请查看：'+str(out/'_导出报告'/'逐作品导出清单.csv'))
    print('原始采集数据未修改。评论及视频公开发布前需要人工复查。')
    if summary['failed']:
        sys.exit(2)


if __name__=='__main__':main()
