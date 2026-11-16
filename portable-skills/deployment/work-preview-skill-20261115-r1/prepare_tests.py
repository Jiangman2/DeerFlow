"""Fork immutable 2.1.5 tests and add the actual 2000-character transport case."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
def main():
    old=(REPO/'portable-skills/tests/test_energy_reader_delivery_20261114.py').read_text(encoding='utf-8')
    text=old.replace("candidates/energy-market-daily-2.1.5'","candidates/energy-market-daily-2.1.6'")
    marker="if __name__=='__main__': unittest.main()"
    if marker in text:text=text.replace(marker,'')
    extra=(HERE/'preview_tests_extra.py').read_text(encoding='utf-8')
    target=REPO/'portable-skills/tests/test_energy_work_preview_20261115.py';target.write_bytes((text+'\n'+extra+'\n').encode())
    news=(REPO/'portable-skills/tests/test_energy_reader_news_stage_20261114.py').read_text(encoding='utf-8')
    (REPO/'portable-skills/tests/test_energy_work_preview_news_20261115.py').write_bytes(news.replace("candidates/energy-market-daily-2.1.5'","candidates/energy-market-daily-2.1.6'").encode())
    print('FORKED_TESTS_AND_PREVIEW_REPRO_READY')

if __name__=='__main__':main()
