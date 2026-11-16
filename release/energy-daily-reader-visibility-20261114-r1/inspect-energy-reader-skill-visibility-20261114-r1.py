"""Read-only postcheck of the accepted 2.1.3 Skill publication."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import tempfile
import time

RECEIPT = Path('/var/lib/portal-energy-html-reader-skill-install-20261114-r1/REPORT.json')
RECEIPT_SHA = 'e47a95e921c403f37c3e8f7d97161c348c35e7da3bb8446c8ecdcab36abe41bb'
TREE = '67b0cc44e9aec87fa4fa12274704f59baa636ccd566e55d8cb79cad23efbfeaa'
OPERATION = '8ca2af7963444e528d5add470bcf608e'
VISIBILITY_CHILD = "EXPECTED_ROWS=[{'path': '.gitignore', 'bytes': 36, 'sha256': '0b38df42d58989002b0a92b957aa0edda1b7fc98f2b8e57178a97e51837a5b7c'}, {'path': 'MANIFEST.json', 'bytes': 13949, 'sha256': '5f760272f547321476ea66c2c8ee2dada0b84aa7c7ae23b5eb33948973930f92'}, {'path': 'SKILL.md', 'bytes': 13634, 'sha256': 'b815eb6435696836eb654db28bbefca1eba4c5a76d8214e5cf62a83e9cced3ab'}, {'path': 'assets/browser-runtime.json', 'bytes': 197, 'sha256': '242b7325d07b9af1efa653cf2dcddff895bda387091593010e8ef3d5ec8a8542'}, {'path': 'assets/history/jkx_new_2026-08-06.json', 'bytes': 2512, 'sha256': '62a1315a5d2ea492ab82f4c55e090108cfa0de02e19767cb561a06999099ccea'}, {'path': 'assets/history/jkx_new_2026-08-07.json', 'bytes': 2514, 'sha256': 'fce29b1eb02d174471532d8edef7cb4523f88db7ec8773300148e8e26b810762'}, {'path': 'assets/history/jkx_new_2026-08-08.json', 'bytes': 2506, 'sha256': '512d4464d044d807d12297fd6afc81809edc8c97db78f3aa5c8777b80e2fff3b'}, {'path': 'assets/history/jkx_new_2026-08-09.json', 'bytes': 2506, 'sha256': '5004af883fe884be0865f2f22148ab2f42ded6a2d94624013f53ca6c3a36f30b'}, {'path': 'assets/history/jkx_new_2026-08-10.json', 'bytes': 2514, 'sha256': 'd4433960be70ed58edf5d9e83794e068aa43c28a185c68cd548ee543ff0d39df'}, {'path': 'assets/history/jkx_new_2026-08-11.json', 'bytes': 2512, 'sha256': '1fc999cb9a8cb1ca59dcdaeba6feeda81dc2a52f6ab3e05928f6ed67d49de96f'}, {'path': 'assets/history/jkx_new_2026-08-12.json', 'bytes': 2510, 'sha256': 'd744ce42b00a9ee8b68d6fc4164391d57bce6f2b62fddb9e37edbf121f10edc9'}, {'path': 'assets/history/jkx_new_2026-08-13.json', 'bytes': 2513, 'sha256': 'e85f20d543b2a387b148bc6c6480627079029dc9e48007f70ab936e228b3f4cc'}, {'path': 'assets/history/jkx_new_2026-08-14.json', 'bytes': 2512, 'sha256': '088e22d891749658ea599b429c9532d366d855db5866d52b824825e7ed98968a'}, {'path': 'assets/history/jkx_new_2026-08-15.json', 'bytes': 2507, 'sha256': '0f56c97c3bcfa68a473518dc84329618c47b2db0056690826b14d8952115c0b9'}, {'path': 'assets/history/jkx_new_2026-08-16.json', 'bytes': 2507, 'sha256': '0674db6c7e37a1b8bfd0e4a59912044b72e907f0ede13da826a20e135c9ce709'}, {'path': 'assets/history/jkx_new_2026-08-17.json', 'bytes': 2509, 'sha256': '340902b50080dde1c5a55361131a923f1a3e8fc384d0fa8c30cf0bfbd0e3e1c6'}, {'path': 'assets/history/jkx_new_2026-08-18.json', 'bytes': 2513, 'sha256': 'a667b675589419f3d04c3850d9329f3cb04eb0bec9a6a5c2b4997ce03a0ef0e6'}, {'path': 'assets/history/jkx_new_2026-08-19.json', 'bytes': 2513, 'sha256': 'f9347cb7472f3b668d489a4c74a3ee8dd5d718cf11ee2ce2e4e8f99af912f0ee'}, {'path': 'assets/history/jkx_new_2026-08-20.json', 'bytes': 2515, 'sha256': 'c520a8b97f656dc201118ee6aced94b7be657b20889bc62ee574d91c659cbea8'}, {'path': 'assets/history/jkx_new_2026-08-21.json', 'bytes': 2514, 'sha256': '50367ad4e847888cdea4fd23018358284e41eb4f06a9dc92918c8bf7df06e9cf'}, {'path': 'assets/history/jkx_new_2026-08-22.json', 'bytes': 2507, 'sha256': '9b734ab44fe03829440a40ce3b811e754c146b6a91e1701b85b831cdb4857c3a'}, {'path': 'assets/history/jkx_new_2026-08-23.json', 'bytes': 2507, 'sha256': '367485236cb8bdcefa0b969383f2013d87c4c2e1d320b809ac126a748856034b'}, {'path': 'assets/history/jkx_new_2026-08-24.json', 'bytes': 2514, 'sha256': 'bcacacfe20b93dac588f820e5e7785f99a2d17508d0c6442f3a8580593787a7c'}, {'path': 'assets/history/jkx_new_2026-08-25.json', 'bytes': 2512, 'sha256': '05b196eaad0f69f6e4696f6d8de86dfff7d19436c20cb9543456909599135b87'}, {'path': 'assets/history/jkx_new_2026-08-26.json', 'bytes': 2514, 'sha256': '1d52467246c5cd9b88c9e886e7960c39195fc6c71174a1c20af93b0ca12d5a81'}, {'path': 'assets/history/jkx_new_2026-08-27.json', 'bytes': 2512, 'sha256': 'c7f035c6dcf03fc51e71987663511b79d7cc1b88f79a7504e32619705a283de6'}, {'path': 'assets/history/jkx_new_2026-08-28.json', 'bytes': 2512, 'sha256': 'ad1e626297a69f2c104bb191a654fc5a21e0b166f511214bd392061ac873c27e'}, {'path': 'assets/history/jkx_new_2026-08-29.json', 'bytes': 2507, 'sha256': '328da868c6c9be0f704216736b9d62e94b361447ec8b70d959f261cbcfca3c56'}, {'path': 'assets/history/jkx_new_2026-08-30.json', 'bytes': 2507, 'sha256': '3c5697589976d473dc806de4a8b1e8d3c36ad29096a8aaa36bf749d06e9e3275'}, {'path': 'assets/history/jkx_new_2026-08-31.json', 'bytes': 2532, 'sha256': 'ce0c37a4416d04b4f7fa629310eb0c365da3542c56cf42fb7753b12d3b540e94'}, {'path': 'assets/history/jkx_new_2026-09-01.json', 'bytes': 2513, 'sha256': '35101f6fbd2f2007ccf8fae048e1de9d715fbc49e781134da79a1722fe819b93'}, {'path': 'assets/history/jkx_new_2026-09-02.json', 'bytes': 2514, 'sha256': 'f90bf217b0278cb7e7717cfc819bc78005b87c41fee236dabca81f75682b2d78'}, {'path': 'assets/history/jkx_new_2026-09-03.json', 'bytes': 2513, 'sha256': '20bcca9f232a554065afbdcddb40905aba6c6333c5706228d388c3028c255db4'}, {'path': 'assets/history/jkx_new_2026-09-04.json', 'bytes': 2516, 'sha256': '039f56754fbbe7e258ba7e77d57cfbba576b1cf037ca5449b6170e141b330989'}, {'path': 'assets/history/jkx_new_2026-09-05.json', 'bytes': 2505, 'sha256': '77a20c94f2222e53b75b50b05725665f1ec8304b96d6ca33599192d39e3ebf37'}, {'path': 'assets/history/jkx_new_2026-09-06.json', 'bytes': 2505, 'sha256': '2dbefc23017aaa922f6d6d800b756785cdbc355e483381a96148845576d95a9e'}, {'path': 'assets/history/jkx_new_2026-09-07.json', 'bytes': 2517, 'sha256': '1213d2fe5469ed130c25e6de818b583164ed05403391a7515e17cd5024abfa8d'}, {'path': 'assets/history/jkx_new_2026-09-08.json', 'bytes': 2515, 'sha256': 'fc60f68fcf650f0b364c6ab9bdf2455ec1a37ad60b1412bfda4f5860c14b8fcc'}, {'path': 'assets/history/jkx_new_2026-09-09.json', 'bytes': 2517, 'sha256': '0f57d26bc63f330701bf55023a2e1d64167ab61a6be700e31ce6c60a53a5b773'}, {'path': 'assets/history/jkx_new_2026-09-10.json', 'bytes': 2515, 'sha256': '787572a42aa98793593bc240b57a0251a9dff071c4f7c3cb3ae3f6d44f92b357'}, {'path': 'assets/history/jkx_new_2026-09-11.json', 'bytes': 2519, 'sha256': 'c5c6016cdfa771065b31b3ab83b1639517ec41e1c47d4d7a49c77f3c17635528'}, {'path': 'assets/history/jkx_new_2026-09-12.json', 'bytes': 2507, 'sha256': '6cad141cf70f0e234a20290d158ec60b65679b9ddc877a13b5b419a2e22d5618'}, {'path': 'assets/history/jkx_new_2026-09-13.json', 'bytes': 2507, 'sha256': '73e9f9a73950a92aa10bb49df585c84ea373176277508e32ad0ee6e6a7783358'}, {'path': 'assets/history/jkx_new_2026-09-14.json', 'bytes': 2520, 'sha256': '544fe0eb110b8f013197831b4ac6d5da9be98aaab46013b2e333758629dc395b'}, {'path': 'assets/history/jkx_new_2026-09-15.json', 'bytes': 2519, 'sha256': 'd2e197ff7831727865a692a3472461b9a5ed27ed622040855d1262e0c4facdb2'}, {'path': 'assets/history/jkx_new_2026-09-16.json', 'bytes': 2520, 'sha256': 'e7600af3ece2461e4f367d2077caa78c07d87cc76adcbfa8485649cb4e492347'}, {'path': 'assets/history/jkx_new_2026-09-17.json', 'bytes': 2519, 'sha256': '1f8f631258b8cbe3591a907ae6efd016b0146f381ad1042d8095dfc236a8915d'}, {'path': 'assets/history/jkx_new_2026-09-18.json', 'bytes': 2519, 'sha256': '7c9ae851f62cbf13414cf3973989c7f25f025b08231a96b3d5185d82403a131c'}, {'path': 'assets/history/jkx_new_2026-09-19.json', 'bytes': 2507, 'sha256': '1a3baf0cd74b74f698d573937e95efc94f14b35059adeac87db384e68cbaf270'}, {'path': 'assets/history/jkx_new_2026-09-20.json', 'bytes': 2507, 'sha256': 'c9677aea1d82580755a50456352fc2fbaa005288feea88ce0987a0678212fa40'}, {'path': 'assets/history/jkx_new_2026-09-21.json', 'bytes': 2518, 'sha256': 'b32b673586bef4657cd45ef774232b3dc3efda53a0e217de0c681bb7f7add591'}, {'path': 'assets/history/jkx_new_2026-09-22.json', 'bytes': 2516, 'sha256': '757de36a6abd00c2296f60168cd112503ae41da59b0cad5cdae172f5c7a16dc5'}, {'path': 'assets/history/jkx_new_2026-09-23.json', 'bytes': 2517, 'sha256': '9dfc5a6f359ea85653dc06bd6c4f3f4d9edf7a4bcc28f8a7715517d359590279'}, {'path': 'assets/history/jkx_new_2026-09-24.json', 'bytes': 2518, 'sha256': '87513e5b564da9594652452bac87d554ab0fec4f37dfcd8b1dd04e84d770b45f'}, {'path': 'assets/history/jkx_new_2026-09-25.json', 'bytes': 2518, 'sha256': '90e99a51b95da1b755cfa9b699465900e1febbbb83405612f5fe100b1a3ce371'}, {'path': 'assets/history/jkx_new_2026-09-26.json', 'bytes': 2507, 'sha256': '3686caa8952f50a12926b64df38b39170e6d115b8073d93e5427b52f6d0e1339'}, {'path': 'assets/history/jkx_new_2026-09-27.json', 'bytes': 2507, 'sha256': '034869ea371540061bf471d069f001432247d76b96ea64cd8be0885ae2905267'}, {'path': 'assets/history/jkx_new_2026-09-28.json', 'bytes': 2516, 'sha256': '0e32f9836aba793b6a7ac3e9a6abca24f8ed66b8920e5bdd5dade63c4ab56c22'}, {'path': 'assets/history/jkx_new_2026-09-29.json', 'bytes': 2515, 'sha256': '4293ba837a5ea7cf12982f16f54f8a742cb554ad2949c8ebfae492eefc5f3ee4'}, {'path': 'assets/history/jkx_new_2026-09-30.json', 'bytes': 2517, 'sha256': 'fff525adbea0a993bef33bd4da55af8fc4147d5ff6f8f75711fe2e8059fa72ee'}, {'path': 'assets/history/jkx_new_2026-10-01.json', 'bytes': 2517, 'sha256': '85c072ce4293fa0e9bda312de84135a1a05871631bda52acebbb01381a02fd2f'}, {'path': 'assets/history/jkx_new_2026-10-02.json', 'bytes': 2518, 'sha256': 'ba66f2687359279f4031ab7c4ed5630fb9825b8699dea6022a8c8a93404f4ebb'}, {'path': 'assets/history/jkx_new_2026-10-03.json', 'bytes': 2507, 'sha256': 'e7e34fdb92b1fbdaddb1459874cc9f3e0dfe178f0a1c4c4faa52d335b01d0a91'}, {'path': 'assets/history/jkx_new_2026-10-04.json', 'bytes': 2507, 'sha256': 'b763414227bf8a4f68e43cad02e7e22584b839b658643e4f379ed61b1ec81a7a'}, {'path': 'assets/history/jkx_new_2026-10-05.json', 'bytes': 2518, 'sha256': '781deacab3f7d163d60529c6fe85eb424db8c2e615e3f1f2026dff8d03eb2b4d'}, {'path': 'assets/history/jkx_new_2026-10-06.json', 'bytes': 2519, 'sha256': 'ab89c60fb3ef7067ec243aba023949c16593ea1128d4063ff14e6357b493c6f2'}, {'path': 'assets/history/jkx_new_2026-10-07.json', 'bytes': 2516, 'sha256': '9ee4363095501bcb5130f46a2f0a3a48d8410aa7801fe5882b48e7c0a8b4b7d1'}, {'path': 'assets/history/jkx_new_2026-10-08.json', 'bytes': 2518, 'sha256': 'b153e2fe69f5913c1eda7058c0f76e3b099c5c84fab920f1770247903974cac9'}, {'path': 'assets/history/jkx_new_2026-10-09.json', 'bytes': 2518, 'sha256': '2bd1ca9282064880e3e7b148ddc19d0a066af12be059df7e9a8517a9cc738e78'}, {'path': 'assets/history/jkx_new_2026-10-10.json', 'bytes': 2509, 'sha256': 'd6bc1c0201e96a53d9ba017ee9a8fca5767663d26d079e3e7538a77eaf2565ce'}, {'path': 'assets/history/jkx_new_2026-10-11.json', 'bytes': 2509, 'sha256': 'db6850df751295d29e7769aa7b31f5c358a122c5fa12b9be2d5bbbfa14cd9e6b'}, {'path': 'assets/history/jkx_new_2026-10-12.json', 'bytes': 2518, 'sha256': '7c9816a3e5905170396256a79cf40683404546cee8abf9c772465e7b7399dc85'}, {'path': 'assets/history/jkx_new_2026-10-13.json', 'bytes': 2518, 'sha256': 'c62d2fa346073c20ef6226fad6435dd41b5ca9fdc5d7c346b301427abee5a740'}, {'path': 'assets/history/jkx_new_2026-10-14.json', 'bytes': 2516, 'sha256': '1c3fa63d6caac4466fcde757fab67523a3ae421dc63a8ed6348e508660b9f8ae'}, {'path': 'assets/history/jkx_new_2026-10-15.json', 'bytes': 2518, 'sha256': 'f1db312bb38e073833c9509297ce4e9890a672b40faff9a426af32b47767e255'}, {'path': 'assets/history/jkx_new_2026-10-16.json', 'bytes': 2519, 'sha256': '9908b22ea324f6e4aa39a69ad6240bbaf6f346f5839ed43d49881a2a6c3d20df'}, {'path': 'assets/history/jkx_new_2026-10-17.json', 'bytes': 2509, 'sha256': '4dd68f560fe2a6a7f984a4c783575929daa48f38e055a7de6d94281c045b37f8'}, {'path': 'assets/history/jkx_new_2026-10-18.json', 'bytes': 2509, 'sha256': 'd281e26d599e6bc2f08070f009c8126ab7a227bea1d9a9d6755c5a859444226b'}, {'path': 'assets/history/jkx_new_2026-10-19.json', 'bytes': 2518, 'sha256': 'b468c2d4a6e17bfbf4e7b903732a980b71e79a7621efb4969086f9bbb0a44bdc'}, {'path': 'assets/history/jkx_new_2026-10-20.json', 'bytes': 2520, 'sha256': '93739324cd2aa2b056d3cf20ddfb8e987c1bf5d4509cf55db32001a247f1fbb0'}, {'path': 'assets/history/jkx_new_2026-10-21.json', 'bytes': 2517, 'sha256': '0140bb8d4c8c17d49a749b08f394fddea8746acf5ae382c7b0eecd5797f030d6'}, {'path': 'assets/history/jkx_new_2026-10-22.json', 'bytes': 2519, 'sha256': 'cc705297d809b91bb610b0753228253d3ca715211fcd04e6e631435bc6255df9'}, {'path': 'assets/history/jkx_new_2026-10-23.json', 'bytes': 2516, 'sha256': 'f86ab3d646a3e02d0c862cb93f021d033496d512c7754c36feba0f7eaa1d9010'}, {'path': 'assets/history/jkx_new_2026-10-24.json', 'bytes': 2509, 'sha256': 'ec90563465836a0c543babb4a4977749b78364dea443bb50c2b2828f9ba424f9'}, {'path': 'assets/history/jkx_new_2026-10-25.json', 'bytes': 2509, 'sha256': 'e95c74c455bcc84cd4bd3e219e3a839e03285ebd0879be319b95fd0a3f599487'}, {'path': 'assets/history/jkx_new_2026-10-26.json', 'bytes': 2517, 'sha256': 'cc0d271ab0a4eb74e834ba4ad8973d9646cc656411a1da8e68c252122bffe14c'}, {'path': 'assets/history/jkx_new_2026-10-27.json', 'bytes': 2516, 'sha256': '7f5877f7e6be9f75deb23dd4fc32bca429d771daceaa9312c5ab77cbd995d4c8'}, {'path': 'assets/history/jkx_new_2026-10-28.json', 'bytes': 2518, 'sha256': 'e610d679e87eb592e52e73cffc2896827199d79a28b3480d34606591b47be497'}, {'path': 'assets/history/jkx_new_2026-10-29.json', 'bytes': 2519, 'sha256': 'e8a6ed9d8fd729afa6a8699be75fb10da905bd92155e215f038340f6de6cebb5'}, {'path': 'assets/history/jkx_new_2026-10-30.json', 'bytes': 2518, 'sha256': '151277472872666298c773cd8aea90e836f552c41b303120672705ad83f82cf8'}, {'path': 'assets/history/jkx_new_2026-10-31.json', 'bytes': 2509, 'sha256': 'd16606d6b621e2d521767d8d9063c40b250a1627dbdcce459e9d37b3a356bbbc'}, {'path': 'assets/history/jkx_new_2026-11-01.json', 'bytes': 2507, 'sha256': 'e2ac1ef6d52b4b92589d05e76de3ee5373df70c6277d43f1c9672418b4ae692a'}, {'path': 'assets/history/jkx_new_2026-11-02.json', 'bytes': 2519, 'sha256': 'f4ceceea35c79ad796f035120e7dddd53606e17eb469a81fad24a040afe4b534'}, {'path': 'assets/history/jkx_new_2026-11-03.json', 'bytes': 2518, 'sha256': 'c685ef3d9ed8b7ddc0951349b4739d8c1d38fae652756126d9f85832b965dcac'}, {'path': 'assets/history/jkx_new_2026-11-04.json', 'bytes': 2515, 'sha256': '051fe6b2ee7da3100c6fc198ff6c21237877819e01a757b233f062bef1bf35aa'}, {'path': 'assets/history/jkx_new_2026-11-05.json', 'bytes': 2516, 'sha256': '660b8cac21968766171ea55bf8ebdd699f4fc9997b5d344fe4a5ee98056bda8e'}, {'path': 'assets/history/jkx_new_2026-11-06.json', 'bytes': 2522, 'sha256': 'bbeb52b48fdae15b8205bbf10af7c2f90c94819baa2b305e644cd1f09f710d2a'}, {'path': 'references/evidence.md', 'bytes': 3253, 'sha256': 'b17ab724a81d67132760660696ef56978e1209564a20dee234eade554ac0eda5'}, {'path': 'references/provenance.json', 'bytes': 402, 'sha256': 'e0f7d37be7235a1d120f310de6f9991077aeec9ea39d8af30bb055680a5d5871'}, {'path': 'references/runtime.md', 'bytes': 4916, 'sha256': 'aee4792f940c6bf653927ee50ada75b0e69630928b28f47d90459944ac311f7f'}, {'path': 'requirements.txt', 'bytes': 20, 'sha256': 'e062756e078c1fa38bdb2796046b669990ab5b5d5e4b5196779b917127b846f3'}, {'path': 'scripts/browser_runtime.py', 'bytes': 10051, 'sha256': '9844b8e4c5e6a6de99a6f7d9cf42f2aafeda1dea5e2b9966f709da7cebf71ae8'}, {'path': 'scripts/common.py', 'bytes': 7380, 'sha256': '3ba16e41756c188d8c67ae97ad03b1b5878e3c48ecd7221aeabcd9e366ac8cb0'}, {'path': 'scripts/daily.py', 'bytes': 57571, 'sha256': '127a6c21c331a17ff970425faca5cc472ea428a59998b0d0f99533acfa241b7e'}, {'path': 'scripts/delivery_text.py', 'bytes': 20613, 'sha256': '3fd8e75963889c6e53a24c15884cdc362636577cee43d865b636a11b6b8f7635'}, {'path': 'scripts/market.py', 'bytes': 12199, 'sha256': '0ade49587ad07af0da852e384090525466b79bd936cd9edaf21789493c8a115c'}, {'path': 'scripts/report_renderer.py', 'bytes': 16028, 'sha256': '2d82ef0d5a81ff5fd21f5695fe94f8cfca7de3df2ea47911127d7c9346b64c8f'}, {'path': 'scripts/research.py', 'bytes': 13863, 'sha256': '0652d2f28854e570ef5b22e8d079f7b1d68d12d217475fee5e28d379cc18a0ee'}]\n\nimport hashlib,json,os,pathlib,stat,sys\nout={'status':'INCOMPLETE','files_modified':False,'browser_launched':False,'credentials_read':False,'provider_requests':0}\ndef need(ok,code):\n if not ok:raise ValueError(code)\ndef read(path,limit):\n for part in (path,*path.parents):need(not part.is_symlink(),'SOURCE_LINK')\n before=path.stat();need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=limit,'SOURCE_BOUND')\n raw=path.read_bytes();after=path.stat()\n need(len(raw)==before.st_size and (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'SOURCE_CHANGED')\n return raw\ntry:\n if os.geteuid()==0:os.setgroups([]);os.setgid(1000);os.setuid(1000)\n need(os.geteuid()==1000 and os.getegid()==1000,'ORDINARY_UID_REQUIRED')\n EXPECTED_SKILL_COUNT = 108\n root=pathlib.Path('/mnt/skills/public/energy-market-daily');runtime=root.parent/'.energy-browser-runtime-20261112-r1/linux-arm64'\n rows=[]\n for directory,folders,names in os.walk(root,followlinks=False):\n  for name in folders:need(not(pathlib.Path(directory)/name).is_symlink(),'SOURCE_LINK')\n  for name in names:\n   path=pathlib.Path(directory)/name;raw=read(path,1048576)\n   rows.append({'path':path.relative_to(root).as_posix(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})\n   need(len(rows)<=128,'SKILL_COUNT_BOUND')\n rows.sort(key=lambda row:row['path'])\n tree=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()\n out.update(observed_files=len(rows),observed_tree_sha256=tree,expected_tree_sha256=sys.argv[1])\n expected={row['path']:row for row in EXPECTED_ROWS};actual={row['path']:row for row in rows}\n changes=[]\n for name in sorted(set(expected)|set(actual)):\n  if expected.get(name)!=actual.get(name):\n   changes.append({'path':name[:180],'expected':expected.get(name),'actual':actual.get(name)})\n out.update(difference_count=len(changes),differences=changes[:16],differences_truncated=len(changes)>16)\n need(len(rows)==EXPECTED_SKILL_COUNT and tree==sys.argv[1],'ORDINARY_SKILL_TREE_MISMATCH')\n raw=read(runtime/'RUNTIME.json',262144);digest=hashlib.sha256(raw).hexdigest()\n need(digest=='cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598','ORDINARY_RUNTIME_MANIFEST_MISMATCH')\n binding=json.loads(read(root/'assets/browser-runtime.json',32768))\n need(binding=={'schema':'energy-browser-runtime-binding.v1','directory':'../.energy-browser-runtime-20261112-r1/linux-arm64','manifest_sha256':digest},'ORDINARY_BINDING_MISMATCH')\n executable=runtime/'chrome-linux/headless_shell';state=executable.stat()\n need(stat.S_ISREG(state.st_mode) and state.st_size==298683784 and os.access(executable,os.R_OK),'ORDINARY_RUNTIME_NOT_READABLE')\n temporary=pathlib.Path('/tmp');space=os.statvfs(temporary)\n out.update(status='ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE',uid=os.geteuid(),gid=os.getegid(),skill_tree_sha256=tree,runtime_manifest_sha256=digest,\n  temporary={'path':'/tmp','readable':os.access(temporary,os.R_OK),'writable':os.access(temporary,os.W_OK),'searchable':os.access(temporary,os.X_OK),\n  'noexec':bool(space.f_flag&getattr(os,'ST_NOEXEC',8)),'available_bytes':space.f_bavail*space.f_frsize},\n  nondefault_tmpdir_configured=bool(os.environ.get('TMPDIR')))\nexcept Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,ValueError) else 'ORDINARY_VISIBILITY_FAILED_REDACTED')\nprint(json.dumps(out,sort_keys=True))\nsys.exit(int(out['status']=='INCOMPLETE'))\n"
CLAIM = 'deer-flow-skills-rwx-pvc'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=25s', '-n', 'deer-flow']


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def fixed_read(path, expected_hash, expected_size=None):
    for part in (path,) + tuple(path.parents):
        need(not part.is_symlink(), 'FIXED_FILE_LINK')
    descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(descriptor)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= 1048576, 'FIXED_FILE_BOUND')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(1048577)
        after = os.fstat(descriptor)
        stamp = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        need(stamp(before) == stamp(after) == stamp(path.stat()), 'FIXED_FILE_CHANGED')
        need(len(raw) == before.st_size and (expected_size is None or len(raw) == expected_size)
             and hashlib.sha256(raw).hexdigest() == expected_hash, 'FIXED_FILE_IDENTITY_MISMATCH')
        return raw
    finally:
        os.close(descriptor)


def ready_candidate(pod):
    metadata, spec, status = pod.get('metadata', {}), pod.get('spec', {}), pod.get('status', {})
    if metadata.get('deletionTimestamp') or status.get('phase') != 'Running':
        return None
    if metadata.get('labels', {}).get('app.kubernetes.io/component') != 'sandbox':
        return None
    containers = [x for x in spec.get('containers', []) if x.get('name') == 'sandbox']
    states = [x for x in status.get('containerStatuses', []) if x.get('name') == 'sandbox']
    if len(containers) != 1 or len(states) != 1 or states[0].get('ready') is not True or not states[0].get('state', {}).get('running'):
        return None
    name, uid = metadata.get('name', ''), metadata.get('uid', '')
    need(re.fullmatch(r'sandbox-[a-z0-9-]+', name) and re.fullmatch(r'[a-f0-9-]{36}', uid), 'POD_IDENTITY_INVALID')
    mounts = [x for x in containers[0].get('volumeMounts', []) if x.get('mountPath') == '/mnt/skills/public']
    volumes = {x['name']: x for x in spec.get('volumes', [])}
    mount = mounts[0] if len(mounts) == 1 else {}
    volume = volumes.get(mount.get('name'), {})
    contract = len(mounts) == 1 and mount.get('subPath') == 'public' and not mount.get('subPathExpr') and volume.get('persistentVolumeClaim', {}).get('claimName') == CLAIM
    return {'pod': name, 'uid': uid, 'node': spec.get('nodeName'), 'mount_contract': contract,
            'mount_readonly': mount.get('readOnly', False), 'container_id': states[0].get('containerID'),
            'image_id': states[0].get('imageID'), 'restart_count': states[0].get('restartCount')}


class Inspector:
    def __init__(self, child):
        self.child, self.selected, self.queries = child, None, 0

    def query(self, args):
        allowed = [['get', 'pods', '-l', 'app.kubernetes.io/component=sandbox', '-o', 'json']]
        if self.selected:
            allowed += [['get', 'pod', self.selected['pod'], '-o', 'json'],
                        ['exec', self.selected['pod'], '-c', 'sandbox', '--', 'python3', '-I', '-B', '-c', self.child, TREE]]
        need(args in allowed, 'READONLY_FIXED_COMMAND_REQUIRED')
        self.queries += 1
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(KUBE + args, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                                       env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'},
                                       start_new_session=True)
            try:
                deadline = time.monotonic() + 40
                while process.poll() is None:
                    need(output.tell() <= 16777216 and errors.tell() <= 65536, 'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic() < deadline, 'COMMAND_TIMEOUT')
                    time.sleep(.1)
                need(process.returncode in ((0, 1) if args[0] == 'exec' else (0,)), 'READONLY_COMMAND_FAILED')
                need(output.tell() <= 16777216 and errors.tell() <= 65536, 'COMMAND_OUTPUT_BOUND')
                output.seek(0)
                return json.loads(output.read(16777217))
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    def run(self):
        listing = self.query(['get', 'pods', '-l', 'app.kubernetes.io/component=sandbox', '-o', 'json'])
        need(isinstance(listing.get('items'), list) and len(listing['items']) <= 4096, 'POD_LIST_BOUND')
        candidates = [x for x in (ready_candidate(p) for p in listing['items']) if x]
        eligible = [x for x in candidates if x['mount_contract']]
        out = {'ready_ordinary_pods': len(candidates), 'eligible_public_mount_pods': len(eligible),
               'ineligible_public_mount_pods': len(candidates) - len(eligible)}
        if not eligible:
            return dict(out, status='VISIBILITY_PENDING', error_code='NO_READY_PUBLIC_MOUNT_CANDIDATE')
        self.selected = sorted(eligible, key=lambda x: x['pod'])[0]
        before = ready_candidate(self.query(['get', 'pod', self.selected['pod'], '-o', 'json']))
        need(before == self.selected, 'POD_CHANGED_BEFORE_READ')
        result = self.query(['exec', self.selected['pod'], '-c', 'sandbox', '--', 'python3', '-I', '-B', '-c', self.child, TREE])
        after = ready_candidate(self.query(['get', 'pod', self.selected['pod'], '-o', 'json']))
        need(after == before, 'POD_CHANGED_DURING_READ')
        need(isinstance(result, dict), 'CHILD_RESPONSE_INVALID')
        out.update(observation=result, stable_pod_readback=True,
                   sample={k: before[k] for k in ('pod', 'uid', 'node', 'mount_readonly', 'restart_count')},
                   current_image_matches_qualified=before['image_id'] == 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8')
        out['status'] = 'PUBLISHED_SKILL_VISIBLE_IN_ORDINARY_SANDBOX' if result.get('status') == 'ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE' else 'VISIBILITY_PENDING'
        return out



def inspect_views(self):
    listing=self.query(['get','pods','-l','app.kubernetes.io/component=sandbox','-o','json'])
    need(isinstance(listing.get('items'),list) and len(listing['items'])<=4096,'POD_LIST_BOUND')
    candidates=[x for x in (ready_candidate(p) for p in listing['items']) if x]
    eligible=[x for x in candidates if x['mount_contract']]
    out={'ready_ordinary_pods':len(candidates),'eligible_public_mount_pods':len(eligible),
         'ineligible_public_mount_pods':len(candidates)-len(eligible),'read_only':True,'sampled_views':[]}
    choices=sorted(eligible,key=lambda x:(x['pod']!='sandbox-0913cd3d',x['node']!='ecs-zdz-pro-0017',x['pod']))
    selected=[];nodes=set()
    for candidate in choices:
        if candidate['node'] not in nodes:
            selected.append(candidate);nodes.add(candidate['node'])
        if len(selected)==3:break
    for candidate in selected:
        self.selected=candidate
        before=ready_candidate(self.query(['get','pod',candidate['pod'],'-o','json']))
        need(before==candidate,'POD_CHANGED_BEFORE_READ')
        result=self.query(['exec',candidate['pod'],'-c','sandbox','--','python3','-I','-B','-c',self.child,TREE])
        after=ready_candidate(self.query(['get','pod',candidate['pod'],'-o','json']))
        need(after==before,'POD_CHANGED_DURING_READ')
        need(isinstance(result,dict),'CHILD_RESPONSE_INVALID')
        out['sampled_views'].append({'sample':{k:before[k] for k in ('pod','uid','node','mount_readonly','restart_count')},
            'observation':result,'stable_pod_readback':True,
            'current_image_matches_qualified':before['image_id']=='sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'})
    good=bool(selected) and all(row['observation'].get('status')=='ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE' for row in out['sampled_views'])
    out.update(status='PUBLISHED_SKILL_VISIBLE_IN_SAMPLED_ORDINARY_SANDBOXES' if good else 'VISIBILITY_PENDING',
               sampled_nodes=len(selected),all_sampled_views_match=good)
    if not selected:out['error_code']='NO_READY_PUBLIC_MOUNT_CANDIDATE'
    return out

Inspector.run=inspect_views

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-readonly', action='store_true', required=True)
    parser.parse_args()
    out = {'schema': 'energy-html-reader-skill-visibility.v1', 'status': 'INCOMPLETE', 'read_only': True,
           'persistent_files_modified': False, 'original_receipt_modified': False, 'workload_modified': False,
           'secret_api_reads': 0, 'provider_requests': 0, 'work_messages_sent': 0, 'browser_launched': False,
           'temporary_command_spools_cleaned': True, 'business_acceptance_pending': True}
    inspector = None
    try:
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        receipt = json.loads(fixed_read(RECEIPT, RECEIPT_SHA, 9671))
        need(receipt.get('operation') == OPERATION and receipt.get('skill_updated') is True
             and receipt['publication']['after_tree_sha256'] == TREE and receipt['publication']['after_files'] == 108,
             'INSTALLED_RECEIPT_MISMATCH')
        need(receipt.get('schema') == 'energy-programmatic-skill-install.v1'
             and receipt.get('status') == 'ENERGY_DELIVERY_SKILL_INSTALLED_VISIBILITY_PENDING'
             and receipt.get('maintenance_pod_absent') is True
             and receipt.get('maintenance_configmap_absent') is True
             and receipt.get('before') == receipt.get('after_deployments')
             and receipt.get('gateway_modified') is False
             and receipt.get('shared_runtime_qualification', {}).get('status') == 'NONROOT_ARM64_PROGRAMMATIC_SKILL_QUALIFIED'
             and receipt.get('shared_runtime_qualification', {}).get('offline_tests', {}).get('run') == 34
             and receipt.get('shared_runtime_qualification', {}).get('offline_tests', {}).get('passed') is True,
             'INSTALLED_QUALIFICATION_MISMATCH')
        inspector = Inspector(VISIBILITY_CHILD)
        out.update(accepted_receipt_sha256=RECEIPT_SHA, accepted_operation=OPERATION, expected_skill_tree_sha256=TREE)
        out.update(inspector.run())
        fixed_read(RECEIPT, RECEIPT_SHA, 9671)
    except Exception as exc:
        out.update(status='INCOMPLETE', error_class=type(exc).__name__,
                   error_code=str(exc) if isinstance(exc, Refused) else 'READONLY_INSPECTION_FAILED_REDACTED')
    out.update(queries=inspector.queries if inspector else 0, observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    raise SystemExit(main())
