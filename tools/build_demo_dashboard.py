"""Build the offline, explicitly fictional dashboard example (standard library only)."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'plugin/hglos/skills/hglos-dashboard/scripts'))
import dashboard


def main():
    source = dashboard.load_source(REPO, Path('examples/Slice_Plan_Settings.md'))
    output = REPO / 'examples/dashboard/index.html'
    body = dashboard.render_html(Path('HGLOS Demo'), [source], None,
                                 '가상 시나리오 · 실제 작업 결과 아님', output=output)
    banner = ('<aside style="padding:16px 24px;background:#fff0d1;border:2px solid #c98812;'
              'border-radius:12px;margin-bottom:20px;font-weight:700">'
              '학습용 가상 대시보드 · 상태와 근거는 화면 설명을 위한 예시입니다. '
              '실제 승인·구현·테스트 결과가 아닙니다.</aside>')
    body = body.replace('<body>', '<body>' + banner, 1)
    body = '\n'.join(line.rstrip() for line in body.splitlines())
    outputs = [(output, dashboard.SIGNATURE + body.rstrip('\n') + dashboard.END_SIGNATURE)]
    outputs.extend(dashboard.prepare_exports(REPO, Path('examples/dashboard/diagrams'), [source]))
    dashboard.write_outputs(REPO, outputs, {source['path']}, overwrite=True)
    print('Example dashboard: examples/dashboard/index.html')


if __name__ == '__main__':
    main()
