"""Prepare a separate read-only check bound to the returned 2.1.5 receipt."""
from pathlib import Path

HERE=Path(__file__).resolve().parent
OLD=HERE.parent/'html-visibility-20261114-r1'

def main():
    source=(OLD/'build.py').read_text(encoding='utf-8')
    changes={
      'published 2.1.4 Skill':'published 2.1.5 Skill',
      'energy-daily-html-visibility':'energy-daily-reader-visibility',
      'energy-daily-html-warning':'energy-daily-html-reader-skill',
      'energy-market-daily-2.1.4':'energy-market-daily-2.1.5',
      '2a09015d31cee41cd2a329fff96062848255d3d49fb7cec8c52d47c939345259':'67b0cc44e9aec87fa4fa12274704f59baa636ccd566e55d8cb79cad23efbfeaa',
      '91ae0c5e6aff42dbb0b73aef146732d0':'8ca2af7963444e528d5add470bcf608e',
      '5dd40639193ff9a9776c235b084977d47219ea2fde4ff154822feca625066663':'e47a95e921c403f37c3e8f7d97161c348c35e7da3bb8446c8ecdcab36abe41bb',
      'b7b27e655f00f3d93202bdb2641403273493e0b3b18308127fded5f1ffcbd7f1':'37423629d9d5f334258168828efed44f890c34493995834054ee5a1425ab9d9d',
      'f162b62883fa82829a30145edace68133ab9a17badf7407d333f54b61d59de58':'16355e886e6023bfd61bcadda8a8ab44bbc492533441a1f035e0cd21209c373e',
      'install-energy-html-warning-skill':'install-energy-html-reader-skill',
      'portal-energy-html-warning-skill-install':'portal-energy-html-reader-skill-install',
      'RECEIPT_SHA, 9328':'RECEIPT_SHA, 9671',
      'energy-html-warning-visibility':'energy-html-reader-skill-visibility',
      'inspect-energy-html-skill-visibility':'inspect-energy-reader-skill-visibility',
      'sandbox-0636d6b8':'sandbox-0913cd3d',
      'ecs-znws-0004':'ecs-zdz-pro-0017',
    }
    for old,new in changes.items():
        assert old in source,old
        source=source.replace(old,new)
    # The ancestor's fixed qualification is intentionally 27; this new receipt
    # proves the expanded 34-test set and the linked HTML text check.
    marker="    source = source.replace('energy-programmatic-skill-visibility.v1', 'energy-html-reader-skill-visibility.v1')"
    assert source.count(marker)==1
    source=source.replace(marker,marker+"\n    assert source.count(\"get('run') == 27\") == 1\n    source = source.replace(\"get('run') == 27\", \"get('run') == 34\")")
    (HERE/'build.py').write_bytes(source.encode('utf-8'))
    tests=(OLD/'test_views.py').read_text(encoding='utf-8').replace('sandbox-0636d6b8','sandbox-0913cd3d').replace('ecs-znws-0004','ecs-zdz-pro-0017')
    (HERE/'test_views.py').write_bytes(tests.encode('utf-8'))
    print(str(HERE))

if __name__=='__main__':main()
