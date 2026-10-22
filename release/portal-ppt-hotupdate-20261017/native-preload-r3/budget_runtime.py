"""Fixed evidence, conservative allocation allowances, and live two-phase gates."""
import copy
import hashlib
import json
import os
import stat

BINDING = json.loads('{"allocation_sha256":"304460fa19e0b62e4193bebd03316950de2324a1d4f6496ad944c83cc844d1d6","chains":{"sha256:01bb7f1275b15bdb231071dff553c5d8ec95c9c692952ea8caffbf9a30d626ec":["sha256:c267fb7ac3d51fe94b7f86952e2a9fc9bdc2229b6071a7cf2964640bb1df118a",[["sha256:acef304eacf1783aa23b1a2a14ad07655cbfa12705b002f5b826b75d080dec96",17412912]],36405248],"sha256:021c46409678d6fbef901542ea86646b5ae027146874e5f60eb0674b874d0dc2":["sha256:bf3d9d3b8a5d3c3c60aee3d0e6acf1eb268c4b4a57bca98a985abb4d7f994fc1",[["sha256:102d191873e30dc514de4e3aad68a8d1eeff2aeea66de554af9e08147ed6cdd4",891]],65536],"sha256:030e224f6e407da12612ec928a4d26e57b9c7d90323ef030a2358df409dbbc3e":["sha256:8c5cd599d9f4f419382beb2f370a3727a770d2c51ac841d70350095b9d469d14",[["sha256:03f2292d91fd8fa5e827d02945186003bda3ed9865a9a8947d3a9b4bfe77ba87",1425]],57344],"sha256:03666d402bbb6a07791fef27c454a8e8d99618d7b649a802c847f39ed3666f9e":["sha256:f82c21e006d974313b725b8ebb220b6844a7682c6639c61f8d2b872e1bf0a1ba",[["sha256:825227403f39b704c112cbdc1583d413b58469c8baf5a84f5200209bd9719c30",3989]],77824],"sha256:055ab12bd547e17a0630037f06a186cf405e3f61bb03a614562ca8f5ab52a8be":["sha256:055ab12bd547e17a0630037f06a186cf405e3f61bb03a614562ca8f5ab52a8be",[["sha256:333125b5cee9fb6718bdcb523fc93b4adc71b7c37ada6146a20c193430e549b9",28123680]],127070208],"sha256:0850f325c373919a7eb20169de1f6a414dd540c23c0152199685965ec294bbe9":["sha256:c9c77c376fbc3a2b17e5f84a4144eaad0ace534a2354d7e1fc9352d086273522",[["sha256:37a5e8ad0d3bf09d522e7540837b91b52aca5902fe3f0bcc45bd915019699de9",891]],73728],"sha256:08ba203f0cfb82a5dc44901e8d349f57442a2190307d484304d9ff6d220950be":["sha256:daa0a98a8513f95491cfed5d3707c6a5ae92ff216f497756744ee02ac69766ce",[["sha256:ed86127c28fcc5be741df935097f94e6f7a78992f1c61a9cece88360f3cd09d6",280083919]],1332514816],"sha256:08e0358d8299afde238183afbaa6fd8792b0cc643206203a3e7f01479a3870a5":["sha256:167d8c391610ed975441ba558d640fb93c4d722f194715a2e8a25ade03aa9bb0",[["sha256:26736b449ddf91cdcf8fed77e9e8992f7292c67793d64425065dee0830ce7e47",2501445]],25739264],"sha256:0a67d68ceb6f8f1441072648c6a0fba508c0206669dc7b63ee4b50468ef64c57":["sha256:67b3e0217e86809e887d198c0f172226c683e578e999a3c04302398849de7c94",[["sha256:58f1b9d5049661143ba9f1fc48b369a4728f0a5db1a69d99a61e205046cc826a",2513983]],10330112],"sha256:0afe05759a2c34effd33eaf1a77b0d02eec74c6211ce874871da78e46d587072":["sha256:107225c6e2a464537967f837402ea97477121919f9567d8fc290bb42ff68725b",[["sha256:e9a4ee1cf21f454d39152a46885a4449d0044a28a8ec6f473140075f95a725f2",2258]],45056],"sha256:0d11b48c626944a313b055ce0c74378b4309a5537db58ec64f95fce5ad9a0d53":["sha256:47699ca4ca3e43478714c0ed6276464f90571381e6e4fdcaa8c7059944ff3649",[["sha256:160c2352aa37f1eef7ab647392050ba96f134e4a79e1ab59b88f552cd74a4a93",281]],57344],"sha256:0fd3ef5a781e88b2971c6dc5064628a1dfc0b906c151b927ffb11a51af7acd17":["sha256:eadc985f74fcca4e1a34576c6717c80f82b620b24f6521c069d5570dc035c029",[["sha256:3cdc64d2ec2c1952d47b21c981c11d770071ba3a1e6c1f35843919d826b09684",3816144]],24772608],"sha256:110f26832cee6bf763e17d5aabd7486f5cee4d38c476a0f1daa61944d881903b":["sha256:2e96b0b499d56dadd49a4a8a0e949ad3e6268b02b2a76c993612d634cb0d47f5",[["sha256:0d27124c6bddab60197f6a2b3a9773867960d37c94496f281499bffb566138a0",2042]],77824],"sha256:117ca0a845aaeeb7e70b18044621f167cc316e24223148c2d1d5954a9f49c869":["sha256:3bc56395810e02e6543aaa1372c994ce95e17494cd908b371683169b80cde6b5",[["sha256:f03163b753cf90b2c2d19edddd22df3f28b3173a47a76983feb0831e8f273d01",3956]],77824],"sha256:129f223c55486a839837850c3834a0a1ce9ae6113d4e1a10c9aacfc59227ba69":["sha256:bfd25ce3d2b46b1650603f04aa30b33445b88afd4bf0c0ccf95d39a46e8601c4",[["sha256:187be5768304d81092e98c75aefd82887a400feba5941161688ce65c6ed83d82",1115]],57344],"sha256:13c98474d82505afbdc1ea391865e5a7056b7e7fff4c348792035b2cc4ed4700":["sha256:73bec230cf6b6ceb87651d74934f0da1fa2e1e64a43f00e3a9dbe4f5cf6bef63",[["sha256:171155e161020849368ad3feb8fb53e3a323ea00aede68492352753f936c2ae3",1681]],69632],"sha256:180bb3b49b84ea1fc361f0cba22115bcddfe4ba4459102567f9cee962b7497fd":["sha256:727c48da8bd1574b54cca2ceb0b82296ad87126ac1397794846fe0ff403c8b8b",[["sha256:f017c74dad54d00685a20438a4f9557ddc786ea64cced0130ad0b1f2cf12c4a6",1039]],73728],"sha256:19f53786e23e01d739952fdb37d6096ba3d755b71a927d69c960c7fe5718b1aa":["sha256:da55c64fd3d827183c60154ebbb3c5eadf7e4e8a3bbf79765d33f5e12607cec6",[["sha256:ee3b6dd95df4c0745b8b8f7e0889204ef6425de1d62c6d721eebb8777dec50d9",18635429]],41594880],"sha256:1b349a3334531b575b01d409540dd8aca14ef29abddacd50f36643d7c949db4b":["sha256:1b349a3334531b575b01d409540dd8aca14ef29abddacd50f36643d7c949db4b",[["sha256:a9986cd6f37dbddae7862a6d4be71683472e7c2ea708e87db14f8a6393c00f00",4187659]],12787712],"sha256:1b73228fef173659973072ae3565a1eff7a73fbe7ae84c4ea3a93e1551a242e4":["sha256:55109fc931c6527ecc38a84cf38eca71cd95c6f03faa7382fbbe0cb07d1539b7",[["sha256:4dfaffa274a6d38896d0788b2c7aec53604713bc6ca7a4da2f68bddcda6b71cc",785091]],1712128],"sha256:1c450d8acaa19a38a5c82fec291e09ddea8bbe859bb9e43b0092ec1b6969a3d6":["sha256:f315e70c4fa1dae15d4da260e030e701ebc69b728473ccd00012d141443c428f",[["sha256:1555c0028a470f369743f53d31f94053190fd1bbcfeb2382ba9f493c3dfdb3ff",1462]],53248],"sha256:1e157c696d3c0bb8299dfe9cd2183e01edb29960772e4e7bf3dfe362308b238f":["sha256:1643ca6117e9649371b9303fbdd663619d725425d3f270fa8bb42358a1026de4",[["sha256:74849c662da757b89cfe8b5406d7beaaf491727ea6843cbba85b8889d1543956",1262880]],5644288],"sha256:1f818a269b17a3141a73499265ea75667524e5f0b27531a4935f3ea6662231c8":["sha256:ecde465d46ccf560c1bc4529d7c3103315b3f9c5f9add5a62e85bda959729605",[["sha256:a035eaaee835042773a4bd05a9afe327cbbfa6fb8f58b3cfd3397db76076e3b8",5475]],69632],"sha256:20e2ea7b405df1b4486b5ad4291b36179b7fa1a8356e6bee252f69757a3f6484":["sha256:73bfee789f6dfec98e4cbebbd734388d569f80a9e5c2710645117d76f8a6bdf7",[["sha256:ecb1821dda6ba74460676c011d8bda3d538b1e2de6588189820d6c4f42ef4aa5",892]],73728],"sha256:23d6ac20fc5be45d13fecd7d753f3731700bb761165f4e2eda8ff727304ae606":["sha256:0c850c8016fc506a3e202f2745f64dc846caea23a6f29f42dde776e21759968e",[["sha256:9fab94e0a1ee11f10d2cc141b1841d60fe5428c68f004489157103a447e5d34c",1060]],73728],"sha256:26460c52f319caf2ccd07736f920b74d8be2261c3ae5d5bbf43064252743ba22":["sha256:c2ff369fc81fc5b293e5fdaa223fb9a251f0d414bb06e37d3f47ec03022dd303",[["sha256:e4811a650fb2d23bf8307bed7e00f931da028291694fc1fa78017a5673fc63b4",2743]],53248],"sha256:29176ffd56dcbb2e6cc96e429399fe7765287788adfa4a83041bc8614a495c20":["sha256:2e8962ef24f5f22ebe443d1ce254321513bdb57068ee4b3f84c3051524f43e3e",[["sha256:8c557539de4ae12c2940b5e8e4e7e61f53cde42c9c0019508b5617c506c8c65d",25301947]],93642752],"sha256:2a17027a96358a1d0f705c78495b956726c5cc08684143ef4f450689bdee8d5d":["sha256:3a11f0d1d76706dd358721ffc514ec39dccc27c040d694ef5eb30b14a997d8a2",[["sha256:8bc9ac3f9ba36ac41ea70011a8fe307b9399ddfb814f63956c7ba742ae840e56",2128]],57344],"sha256:369568092969c6a7b1642c31db08817d4ae08390cdc9c4cc9f186f6a18907497":["sha256:0efd8bfd638ffd4a8aa67557834225a7700de5324bbf92cd13371a8177088339",[["sha256:14b0c7388927e5bb2bda04f00a42bbd2fd20e5b262f1836525a5121d50ce52f7",2893]],73728],"sha256:36d57716775ee2d8ec13e20608c319f1e91a7de52306f7b43501e8bc90cdf687":["sha256:f1f7131921cacdb765b5d40523028ed8883d1126445fd08b8705fe6159b85dc1",[["sha256:5918c14b04aaae208c3fd7542a5ddbe45de3adc6d42da84bb4795f3b25135ae8",2038]],77824],"sha256:3c3c5556756560fa6296210a132d1bcb02578248b6bd292a0a2a2793a5ed9da7":["sha256:721fa33420f50f37b47e07e3680c6fdd4790bce2d11ae5d58419a7df988fccd4",[["sha256:5178256f396bb556aab3006b443dce8ba6c5a9863abeb0b757ad7319af1b2238",250]],65536],"sha256:3e3c9c7942dd1bd89eec9cd317220cd55860cdc10fb16cca7f0cc2c4fbbb4ed0":["sha256:633a86dc99dd4b72c8b3c03fa5ccdebb07078bb1af687967cf29d8f9e5d62ec7",[["sha256:83aa9306bffd1cd1345d621e1ab82b113bed838bfd85031bc9f6fbe648ba925f",26194]],180224],"sha256:41b549d99886cd89272e9eda2ea11836cd934ffdd8ae906f78ee80361c9b7dfa":["sha256:e8fcd26ecfdbc6aa840c866b767027c2dbabe6485da839b775972f0333ce7e0a",[["sha256:f80c2f5ea2f3a9348bfe085d2d6bd0f3be203a68cdede4451b02c688c3d47d2f",3369]],65536],"sha256:449f76012dd46e28c31d1615006106dc46fbf07d014dec3b25a1d0514f7445c1":["sha256:98a86b50fc8d9605cc0e1794cf298a62bbc8fc052b4f11ac718d6fcb48c51b90",[["sha256:dd8fff778ff6006ec901922031a5c00067338c079435bdc790468d359b9797c2",13609392]],56168448],"sha256:45cf2cc76355196fa4db526e8e8deb928fd5f265041c266e9db5fc1c77ad0a48":["sha256:170b92e50faa4f53a9f086ba3953c0bf46da062fd388d89734a2bc3ab3626922",[["sha256:7605e895d80df0da4f897a52e53a58884bdcda2008176309f007fbbf18aff578",4080]],69632],"sha256:4a4619cf5f8e1bed7f0bee628ab271f1ec98850a6626fc13fe64dba18fa53533":["sha256:c065cd27c1058cbcf20505cdd0bd4683c3f4bdcbd3829198c1ec1cd66d736e6e",[["sha256:dbdb709b7b7f04d043a773555a4f5f1cae4a29fdde3dbe0b1d94a27c24970109",186]],32768],"sha256:4f0dc140dc7acd2c99f1782e1d2675f63c8377187bda5380db73552854d58948":["sha256:9389664b1beb2de58e74e265eb215a2e376490fdad343bceeaa0711f98990371",[["sha256:a6515d842c52921aeb088f2c7b6675845c67850c5ba2c53bd4c5929b63f44d81",32987760]],34742272],"sha256:504f2a3b0842c57145b1dbf5d086015ac546a517b5f6f098ab6776a1c7c9447e":["sha256:313ad79ffb021b780b84389b9ce101fa9f7e41634299e359ef09d3220b0bf677",[["sha256:66fa63c7a061939c4346f9c3c1ada45434b276dd4e4274721d6bf13cb8adec6e",444]],32768],"sha256:52bee43c860b0bf1c0c8e5d5c6f806a1798ac9677c89de97e3471bcc15807b7e":["sha256:a745c7c84d024d28548f8045d6bd5e3107f3801414901a65d61403a90d7409f0",[["sha256:3172ed6a94852a16d93f87b100503e09c5b0be6e068fd12f1af68955a42fde07",1019]],65536],"sha256:5485a99f25d42f5e27a942e729cdf4622f16007c30c0c542397df334e356ce59":["sha256:b5a9f36ccf430b8434255424203a0b9fb04190005ee087ca76670f8cbf94ec17",[["sha256:6c3d6fb4550307d757b8f9f48c981d4087808f25bf71fcb84e351a9d6f436331",1697]],57344],"sha256:59c541bb1c0c073aeacfe5dbf8273cb97ab250117bd13bbc1db44a428ba8d7d5":["sha256:9949afe792de50eb9c6c16750495b191ea3cee16317e47a183c7907e35640a07",[["sha256:41babfe8a1c70f49dab146636bef02aa329192b99a79a62622c691ba49767f87",13761]],110592],"sha256:5d7566dfff486873b9900d53aa270fa562c7ea43f8e360f08e40f9cfb983966b":["sha256:a2f8243094c478fd5abed007e5deb4492f094019a93b4543df7fbf6bbc89da64",[["sha256:af92cc56aff1d198ab2a38dfbbc3b7adebfee6bc003f66d3a31572e3158c88f4",1406]],57344],"sha256:5f22ee937f5fe50de5a51a468df31aa39e93e73fded578259e8ca052522dd10c":["sha256:aa5ab9da152cc64b7cb99501aa8488bfdf5e88820b5d1ca7d7f8169e9340b45e",[["sha256:824a71a0450a5d3cc8c67524416e8a4ff3a453680d99f662d97bc3072c8399aa",1060]],73728],"sha256:5f7cd1c0eaaa44d635fd57842d4ab598eaff9218077a873b74f2ec9cffdc6564":["sha256:a7defa5e12f6b9ae456e652b54fd29333008633e303c47cea3982dd67e49e673",[["sha256:95ece32a3c17f2672d4249aa59b955c78f3492f220e54d12b65df969c8e63a6c",1298]],57344],"sha256:63ebe161a7078f5626f695426a731622d7b2e0e0e08d7d54fca28aafecfb43bf":["sha256:7c034f14e1ce732434bd7d3e3f64339aba4a8028e0499e2543ba45bbcc022a40",[["sha256:55c766948c8436c1c9be04ed01b589f28f41887e7159fe7947abf6b2467676b5",3027]],65536],"sha256:687be0fee5b3d716e79ab37b2d7a62f98cc19117872dd3c802648ef3525ef9e1":["sha256:839e8fafadca6e1c4bdf83b3191fca736a166be8763e17690cfa89fa302cebf6",[["sha256:6f9cbc23d1357935da18a0fff522afb4f53f7d0a49470dedec178a877b8daada",785097]],1712128],"sha256:688be36498c806cf397042cbbac27d5e5bf55aa8b659e1263c221a78bef32d19":["sha256:9755b9e7adc08aef98732b8040d8687b0fd57a80d768932e81a2adea90069e73",[["sha256:27c13573f7af934af247ae7043c87d455ebbca4da63a0bf2c38240ab1bab1ed2",16576]],122880],"sha256:6b79e0ed3dc34d8de875185828fb08941ab3b65f37aa07f24a2487d14abba999":["sha256:e9c13e6ab823cf6a6a8b62009f51cfd66e6b5c45c356969c5225f1041ff02c64",[["sha256:9b4b81bc5aa6763691b644c31fdb9ba543b4f6e3fc32cf7294a35382abf125b2",2240]],61440],"sha256:6cd49887f4ea83a3962e40e34c073915e0bba8cd07c06bc4c00bb1ef25e1a3ee":["sha256:13a441c9d3fbee1a9b75922de44b4dabb4361af71a4d92f62e6cd97fb9dde567",[["sha256:df2714525732b9c319141446c60af713c424c9de848f7e09f210bcee0f843210",9068]],69632],"sha256:6fa0d4cdfe60ad4453079b7cc35b6a6d185c24e5a76f0900b46f42b4b86c4328":["sha256:e7a33e8f96085a4cc490455bb50e0a16b138c40712771cd88538a4b5c5e03f8f",[["sha256:37ad325242f419c4eab815fddee889d189fd1df8b7e954e30b0a8153301ae789",13506]],110592],"sha256:72885b684ad16e9f29c1e131de427d4cedee5e1b0188a177755b56037ad1928e":["sha256:2f9d67b0d95e9960694c793fa533a5a2a98a3a16169ce1abcde37669a3142351",[["sha256:2991ffeb8d6c3708dd64ba5da40b8e4f7196922aca0672d669f51632186be41b",3047]],65536],"sha256:74f7cf4ab8e2f0af870dde8b527d38ee72dabe999536e4891faaa110cf3082a3":["sha256:f155ed7174a4a7265d8c74dff8cbde3f1edf3fe073078292a5a76208f4121830",[["sha256:f155ed7174a4a7265d8c74dff8cbde3f1edf3fe073078292a5a76208f4121830",256000]],557056],"sha256:7c28b6ada0cd9db38b90c5d0154377fa74e8359259cb645e6973176a044d5731":["sha256:8e1b6be65dbf9a3eca29a54928105950c5ac3a5601701a31f682aa98122beefb",[["sha256:a3dc1b5c1afdec7df9c5ed2a1211f6decd3459bded2ce6f9c4faf4f4dc4cb556",115]],16384],"sha256:7e206610994baf737b9d97e0a96b2c1d0184986cd878bf42cce7fe3c2131e322":["sha256:5f0bc8f78d55b85c33ef5d79c45262d5d67a3a2e4445ceca0e1cc3aedc0efee3",[["sha256:c5be7387255910efe8f39c8c2dc00113288b80fa43b725b495fb9cd14df188bf",915]],65536],"sha256:7ee6d882d6a68f1898f730ba354e2b5096f5ce015cc933a68e734a094a11de3f":["sha256:cdaa1bd6088bf1e0ef7b62e9ce57b133d76333090458c792c4e11020ee9bcd5b",[["sha256:ed25b6ee8c6b1680d6f0e0181bd86d23edce7e8ebdd4a93f4f639a9360d7de6f",5493]],69632],"sha256:836aac8eeaec28415d0bac3e26327f388ca1e5f44ffeec5ba549d5cf98767f29":["sha256:4cadc54bcacb5bca36fa2fa380a983ae79e41a13129dd272ae19599056831ace",[["sha256:6503ba45d478aa2ede3a544fbcd4dda05132670914738fca1d66622b52fd9ee6",3426]],49152],"sha256:83c1f4fe05bb96aef4994465e548ddff533f16414eb94f4880a48b8931fd0b40":["sha256:04535fe947d4b7a3a17d625221acdce7381db6407cfcfa93e1cc3b5cf41d2aa0",[["sha256:767a5d0a5d8999e3ba3b8936a268377c1676eb9257968240036311026abf78d7",3348]],65536],"sha256:8b2bf8f8739c5abee2703bcb68804431c3ee1b87b9726f6f0321a903d2a66972":["sha256:4cefb5e455a35f822c59cbcb4f10dd7e69b06f55fdecb183a4ffb71e74c6a52c",[["sha256:4cefb5e455a35f822c59cbcb4f10dd7e69b06f55fdecb183a4ffb71e74c6a52c",225280]],417792],"sha256:8cbcff1cad0a308369e4eb7348cbc8cabc2ded888c210d03d38157d337c2bd8e":["sha256:f5f094d194e0cfce56790d0894bf4ddb8ca8af1d1c5634589148b3839b36ac52",[["sha256:a2a86179c69782858b1418b21ea0ef22e528f07afc45402b962f1f11dcd4d11c",355]],65536],"sha256:8fee5a5a78677db622a6f61f1963242aa15a9da6d7a6c9e614e6557cbb7b400e":["sha256:15e6ad254677034d46b7a9e3e3932906936d29b47a4867d0c5a1d2afcfd9b4f0",[["sha256:4c78a217d9aa212a8f0c418326a1915fc18a3741378ade587f5d6e8d399c53b6",45326027]],122200064],"sha256:9004532eb0c69898164281502c47345b0357134b03bacfd89f61e3387c3c45f6":["sha256:aa3680a431d0e4315fd9772ce000c1959a49da6e0cdb17612c6f908ca891463a",[["sha256:330d0a1ea4a95c2c4c408c5aeb105b9481e135a969b7d2f2d8cc1f619d1dd3ac",3666]],73728],"sha256:92cd897360ecda475c86fef0c5493575075eec15fd4c4e908e726ff452174eb8":["sha256:929f3d48a416d6baedf0259b82c316ffff28b2194dbdec492ad32607df2ddb88",[["sha256:52c5db11cf794b58a58372c1c7e8ab82716e8d7bd5d91e8220d94f1ee4e8a4c4",1715]],57344],"sha256:933fb3285ccddb5ba1b695ba79ace24ed8c7294cf0da462f1386ebf3bb454218":["sha256:519488fb4eab43de613210ebb8225138002c18c9e51551417294751263b3be13",[["sha256:32b5c3109f30a358c67936f4be36b9f8079aa897fbcf50ec01466456baa7967a",8889]],69632],"sha256:9d9b09a6cd37e0ae3c25e5c56d5ea4d58cddb19c507c18113dee01472757870c":["sha256:8170a15df8406188ddaf1dfdaaea7f0fd284fba9a13a96c8918882bcaabd4156",[["sha256:857257a6a4bfec7a38e4a51db3fc57be0fc5654a69a2b789c36a1b42e9ab0d7c",321]],57344],"sha256:9ed7361c68353af5a0ffdeedce64961943ee0187a3c90cb17844532bc6a9cbcf":["sha256:dfc4bb8871cc775c1891e6523905a4ddca001cd9a360524a234a04550a792bca",[["sha256:518bf8caa0841c022e6eec22849ae5574730a6b83ebb735a09230d876043345e",9099]],90112],"sha256:a154ea283388f9d39ac62e9986a10f96256b12f1cfcb2361bccea04e8c1db972":["sha256:778dd37b10cf49016eff1ff7332d0745c484e51e699c23d94aef4615c007fcbc",[["sha256:9c03644a3b8c887c342ad60399a7cf0a333df8cb40f71eb4e236675070ff484f",794]],40960],"sha256:a691923a8cd920bf0ed8bed3154ab68b59f0762b36d6cd78635490e4b2829cc2":["sha256:ae2454bf5f3de266493eab01070043afc93e765bbcd56da18cc406e036741f6e",[["sha256:5c014639c3769e74d351c08e06cc063c842bdc58fd76830cb1655623ee929306",1621]],24576],"sha256:aa2173d98cd11e29559e083fdb7e6c02fcf00150a87b24ca4bba981afbe98410":["sha256:937ee989a15171f8c7fdaa5ea06b7b484b007a806aecc37b4bd7d5b6bd5cbbcb",[["sha256:3fe6c57c46e975eed4bb8a76264e85dc4c146285ad8833e2694988b1d7af5634",5828]],73728],"sha256:ac913cb9722aaff5e9e391fef5ca38d2a6022292e805d424308fb5c64a2bc61b":["sha256:85b13c525cd45881eab55b2db74468a78556296b8ec1934eaaac4a63bfe24ba1",[["sha256:b657720b1aef7bbc2cc93e6a1a6e42cda3a3fc8d813d014e31e9bc6ac11bc3b9",1822]],32768],"sha256:ad59ac968c8c1f180ef066d0888fd5d5b802e822d408a1956684cc9c7f63bf84":["sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef",[["sha256:4f4fb700ef54461cfa02571ae0db9a0dc1e0cdb5577484a6d75e68dc38e8acc1",32]],0],"sha256:af2a7a3d697edbbb219ad92166bc1011d15f7a4656a99e577c9a6e1f9e747834":["sha256:ea2452c8dbff5a2059039b766904eec6580f8dee63a7a126244b095e3407f238",[["sha256:e9da643a27d70949f900973546ede3310eade7ce57faaf9518033b3880868e88",1034]],65536],"sha256:afbdbc5c1c9c2363f5b72e861aba21430944d49f652bba5122330c731af60b62":["sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef",[["sha256:4f4fb700ef54461cfa02571ae0db9a0dc1e0cdb5577484a6d75e68dc38e8acc1",32]],0],"sha256:b098dd875d24a9bb9b3d71e8d1f6b1fe8fccff9c8b19e2c9c036de6b64ec6184":["sha256:dcb79808b6efcc214a344717e53366c8bdfe72e5e5629302332b7d46d971cbc9",[["sha256:dcb79808b6efcc214a344717e53366c8bdfe72e5e5629302332b7d46d971cbc9",204800]],389120],"sha256:b21b954961f8321e3c505f0c734962ee3c2b8f9f46be4d8744cdfa31cdbc8b85":["sha256:7ffcd99f618e1d0a5720250a4efdac21852d13eb89051004cbe18c8dd94cb691",[["sha256:fd0abd8225aa5e0993e49e492b3d866b71873794c318423326c49f47047304f9",455598091]],1351745536],"sha256:b3b1c27ea481e9e69e9a87a4b5976739decd12cd3b087fbc306e4054e9f1c145":["sha256:e684b36a88e4418f68c35cfaab13837b79387cc658521cd4caaf4b5082c1a096",[["sha256:87919ed734a39bc185155d715bdcf2ce6c52d112ff642650d9e0e35502b481e9",55602437]],170741760],"sha256:b66324a2fd16aa3bb1744429d583bc98d74f80db3c32e5fc47f4caa3f1859914":["sha256:939562142c71ac2b973bec20b38a2ec55d83ebbfaaefb0aecc1046b8cfe534b2",[["sha256:2c2449cc10984b079887425aff68c7cd966623ad9e24e52bd819ce796bf06fde",2223]],61440],"sha256:b70f0c2678dab2e351dfe98375664defd946a2ba482b6945f89290f647d7840d":["sha256:7eb76629460e6ed395bafc1a4f1e6d78d296509e8a54dfc57c1dde5afaccf1f6",[["sha256:c0c221009b7a1dda31a6c6c86c5f63e5522c86a97cdfed0168c3383886efa69a",2824]],53248],"sha256:b8aa435d0d15bdb8a27fd284f3358083557e253d3c2a62bef1aafa34195995a6":["sha256:c92c65b915db5c2a1c46665a5e0a1b1487b2efc9c161583d8becda4695248773",[["sha256:921779e2ac5528e19763274cae7d104ce7794e161e0c6335edd153c24048015f",1038]],73728],"sha256:ba224a9c28d226d0c34e3e16f66c1e228bad281384a1b1c2b323cfc28ef88a6d":["sha256:64b3796025f7bfc88cac1634a103b50909ade2ac97a6fda72b8260731244c046",[["sha256:de081f311725a32851ef8a07715eef2cd3c33b1d7a9b61b6a2e603c800291c0d",16849]],135168],"sha256:c586be54d91e2057687eccad754b860fe3cd990e9b0ac47a36ce7f19d06b34ca":["sha256:732a60848f684fcd87e26f0f872a6e5ce8312aa31cb3144af13eb07b85c4608d",[["sha256:15c28a0fd6ad43aceeb5baaecc9f10523bf6c570c14bfad856282a0015fa65c3",8146]],94208],"sha256:cd49abea16d890afe51820be42a66e3c25e677d29295b5952816054e26afc901":["sha256:9f19354b285fefd2ea9c32f3f1c06a228c8436017c6f0c22882d1a12839ffe16",[["sha256:9f19354b285fefd2ea9c32f3f1c06a228c8436017c6f0c22882d1a12839ffe16",40960]],118784],"sha256:cda99edf6152fa7e578d4d6e43bdcd756a92642042d7db00ff70e41cbb145b2f":["sha256:25a4d6366547614900ac657b2d68ed8169d9bdc3f21a7db74a884141ad29ad05",[["sha256:7ec472d8fb6e1d19ea40dac2e3f37187db80fd2b7e8b18315abe6cc683f1de22",7292]],65536],"sha256:d435803a8226922f2a5900e10c6e11eac9630b9ae56fe2c7f941b66bd55ec512":["sha256:8ac97561bced07092db72428f650384ce011c06b70ece342a20014c67b799e22",[["sha256:0d323875ccdccb394e866e6cafea6562df39bde9f4eefd2cd6b3ef74c6dce8c0",1145]],49152],"sha256:df5baa9588441dd2b69231d7673cb9f537d6ff4c7b9769b588379c5b68528740":["sha256:50be3c142008e0a395657704667768c6600303e8902dc1df513369efedce8840",[["sha256:f7bd74e27ad83d6042dd6ff427ef01ca867a1c819d56cd50397c3316046b09c3",335]],57344],"sha256:e964e6780b947ac0a823afe47c92750eca7fb0fccbd684003c3ce39f9b0c53af":["sha256:c051ee77679d2f410d0d7334e6ea143f35c622baeb7eddef2a24537771ac5838",[["sha256:b6490533e7d8247b906a178c14559f02992d65ea4f30bcf6f69010ae25ed901a",3647]],65536],"sha256:ec0c47ba9a3360466e0b168f6a9869d9adbde5130f235aa5462f25694dac6b69":["sha256:c6e7e32d049b15526bcdfc4436811501e36af99f5e25af2bd8e9b630adc2a779",[["sha256:2e260720e2dcd004911f01253912f4e0687e20ba3144f27e473eab8b4511d452",129943]],258048],"sha256:ec56d0504de549cf4564b13073bd1fa762c99a15ed658681c617349440088b24":["sha256:cf9a43e098a9fc35f47a1457a40aeee1c1855b78905960b67363d99559671a5f",[["sha256:0cf45dfd1ddcbc8d938dcb1a18ae5ef8c71a18712ec810414a16cdb26a1aec04",12015]],90112],"sha256:efdbcc186199b8149feaa65528165cdc137d2ab97e64fd9e105b787e62324086":["sha256:ff10d6e6d5873b5cd27b6180b1d2b58c0345486a6c335fb81a1a3460008cc8e8",[["sha256:5f2623bce37b5811eec4252e52833f699027474bbba7abc2a56d8a0c893d9b37",5382640]],23834624],"sha256:f0a2ca7b675e752cb201b58bf53bced046297ee335ab63a144df938de1035ee8":["sha256:7c57194335c860af393875168905990323de361fc23c41746429b0125ba97aa3",[["sha256:9b86266fdd302fb9414c8e6b5b996f6ad5899a573ee075e4945b86837386b0f1",340]],65536],"sha256:f1512b8fdfcfdbec2dff96dd96db7fad9da86792d3b57170a0488864f443ae60":["sha256:482cb64a43bfc49413cdbf784a0e88e53b4cab5026d56576e303ef6cb3cd76d9",[["sha256:b20f7ab1582107a45b923350d563e41ea858a7153d121ae42bf7be2085cc9926",93]],8192],"sha256:f970e24b4d2beeb3e081b021cca56fec44593566ddad24956201b42dea96ad17":["sha256:953eb97e4aafc87c2196b802a61ba305c738077e303d78ccd47b344b8c80f60c",[["sha256:953eb97e4aafc87c2196b802a61ba305c738077e303d78ccd47b344b8c80f60c",143360]],376832],"sha256:f9cbeed7eebe3203cff155dd694e501a916afc59293658b4a6466b1c1fc18a93":["sha256:9bdf76231edf0ff7a17428821c9b4172cf060ff62ce1960ae02902a983eeb556",[["sha256:cb0044b2c402d1400a391c4b2a8d8a39e426bada28deaa771b274619c81e46e1",4102]],69632],"sha256:fe623a2182f48a575194e4e65c68f72a6d88b4171e57bb03bf21e912aef22f29":["sha256:595dbe00d433ad4d236dfcb4a23a23575e96f753ef14fc4058e2f8e5bca7638d",[["sha256:08b6b33d4094b49af12287cd55dc10c7a6105498e07ee2c726a9aef81d978e89",5902]],81920]},"hardlink_review_sha256":"3cd5e14cc42683514644e3e5ede2c7abb1f9fdb60f70df4bf010306c555d7496","plan_sha256":"e5963c99260ee90f690f7ab0c26e8cf41988b752e74f1211cac22f90b4041ff2"}')
GIB = 1024 ** 3
HEADROOM = 2 * GIB
METADATA = 64 * 1024 ** 2


def require(ok, code):
    if not ok:
        raise ValueError(code)


def committed(plan, snapshots):
    found = set()
    for image in plan['images']:
        for layer in image['layers']:
            row = snapshots.get(layer['chain_id'])
            if not row or row['kind'] != 'Committed' or row['parent'] != layer['parent']:
                break
            found.add(layer['chain_id'])
    return found


def prepare_plan(plan, accepted_report):
    """Caller verifies the original plan/report bytes before invoking this."""
    require(accepted_report['plan_sha256'] == BINDING['plan_sha256'], 'allocation_plan_binding')
    report = accepted_report['target_report']
    first, last = report['samples']['before'], report['samples']['after']
    require(report['relevant_cache_stable'] and first['content'] == last['content']
            and first['snapshots'] == last['snapshots'], 'allocation_cache_not_stable')
    base = committed(plan, first['snapshots']) & committed(plan, last['snapshots'])
    amounts = {}
    local = BINDING['chains']
    host = [r for r in plan['images'] if r['role'] == 'ppt-master-agent-host-forward']
    require(len(host) == 1 and len(host[0]['layers']) == 129, 'allocation_host_shape')
    host_tail = host[0]['layers'][-1]
    require(all(r['chain_id'] in base for r in host[0]['layers'][:-1]), 'allocation_host_base_not_cached')
    for chain, layer in plan['chains'].items():
        if chain in base:
            continue
        if chain in local:
            diff, encodings, amount = local[chain]
            require(layer['diff_id'] == diff, 'allocation_layer_binding')
            amounts[chain] = amount
        else:
            require(layer == host_tail and layer['uncompressed_bytes_upper_bound'] <= 8 * 1024 ** 2,
                    'allocation_unreviewed_missing_layer')
            amounts[chain] = 16 * 1024 ** 2
    for image in plan['images']:
        for layer in image['layers']:
            if layer['chain_id'] in local:
                diff, encodings, amount = local[layer['chain_id']]
                require(layer['diff_id']==diff and [layer['blob_digest'],layer['compressed_bytes']] in encodings,
                        'allocation_image_layer_encoding')
    require(set(local) <= set(amounts) and len(amounts) == len(local) + 1, 'allocation_missing_chain_set')
    require(last['root']['device'] == last['image_filesystems'][0]['device']
            and last['root']['device'] != last['opt']['device'], 'allocation_filesystem_shape')
    result = copy.deepcopy(plan)
    result['allocation'] = dict(schema='portal-native-physical-allowance.v1',
        evidence_sha256=BINDING['allocation_sha256'], hardlink_review_sha256=BINDING['hardlink_review_sha256'],
        physical_by_chain=amounts, filesystems={key:last[key] for key in ('root','opt','image_filesystems')},
        maximum_metadata_import_bytes=max(plan['blobs'][r['manifest_digest']] + plan['blobs'][r['config_digest']]
            for r in plan['images']) + 65536,
        strict_filesystem_upper_bound=False, policy='4K_ROUNDED_DATA_DIRECTORY_SYMLINK_OBJECT_ALLOWANCES_PLUS_64M_METADATA',
        required_cached_chains=len(base))
    return result


def parse_content(raw, wanted):
    values = raw.decode('ascii').split()
    require(len(values) == len(set(values)) and set(values) <= set(wanted), 'allocation_content_filter')
    return set(values)


def parse_snapshots(raw, chains):
    lines = raw.decode('utf-8').splitlines()
    require(lines and lines[0].split() == ['KEY','PARENT','KIND'], 'allocation_snapshot_header')
    rows = {}
    for line in lines[1:]:
        words = line.split()
        if not words or words[0] not in chains:
            continue
        require(len(words) in (2,3) and words[0] not in rows, 'allocation_snapshot_row')
        parent, kind = ('',words[1]) if len(words)==2 else (words[1],words[2])
        require(kind in ('Committed','Active','View') and parent == chains[words[0]]['parent'],
                'allocation_snapshot_parent')
        rows[words[0]] = dict(kind=kind,parent=parent)
    return rows


def reserve(row):
    return max(20 * GIB, (row['capacity_bytes'] + 4) // 5)


def budget(plan, content, snapshots, phase):
    """Reconciles unique content/chains; already allocated bytes are in live free."""
    allocation = plan['allocation']
    cached = committed(plan,snapshots)
    amounts = allocation['physical_by_chain']
    required_base = set(plan['chains']) - set(amounts)
    require(len(required_base) == allocation['required_cached_chains'] and required_base <= cached,
            'allocation_cached_base_lost')
    missing = sum(size for dig,size in plan['blobs'].items() if dig not in content)
    physical = sum(amount for chain,amount in amounts.items() if chain not in cached)
    tiny = allocation['maximum_metadata_import_bytes']
    if phase == 'BEFORE_PHASE_B':
        require(not missing, 'allocation_content_closure_lost')
        work = physical + tiny
    else:
        work = max(missing + plan['maximum_outer_blob_bytes'], missing + physical + tiny)
    return dict(missing_content_bytes=missing, remaining_physical_allowance_bytes=physical,
                full_archive_ingest_scratch_bytes=plan['maximum_outer_blob_bytes'],
                metadata_import_scratch_bytes=tiny, metadata_allowance_bytes=METADATA,
                incremental_peak_allowance_bytes=work+METADATA, committed_chains=len(cached))


def stat_row(path, expected, fd=None):
    s = os.stat(path) if fd is None else os.fstat(fd)
    v = os.statvfs(path) if fd is None else os.fstatvfs(fd)
    require(stat.S_ISDIR(s.st_mode) and s.st_dev == expected['device']
            and v.f_blocks*v.f_frsize == expected['capacity_bytes'], 'allocation_filesystem_identity_changed')
    require(0 < v.f_frsize <= 4096 and 0 < v.f_bsize <= 4096 and not v.f_flag & os.ST_RDONLY,
            'allocation_unsupported_block_geometry')
    return dict(path=path,device=s.st_dev,capacity_bytes=v.f_blocks*v.f_frsize,
                available_bytes=v.f_bavail*v.f_frsize,available_inodes=v.f_favail)


def check(B, profile, plan, phase, sealed_fd=None):
    require(phase in ('TARGET_PREPARE','BEFORE_SEAL','BEFORE_PHASE_A','BEFORE_PHASE_B','MONITOR'), 'allocation_phase')
    expected = plan['allocation']['filesystems']
    image_expected = expected['image_filesystems'][0]
    image = stat_row(image_expected['path'], image_expected)
    root = stat_row('/var/lib',expected['root'])
    opt = stat_row('/opt',expected['opt'],sealed_fd)
    if phase == 'MONITOR':
        for row in (image,opt):
            require(row['available_bytes'] >= reserve(row)+HEADROOM and row['available_inodes'] >= 100000,
                    'live_storage_headroom_reached')
        return dict(scope='LIVE_RESERVE_ONLY',capacity_pass=False)
    paths = B.image_fs_paths(B.parse(B.capture(B.CRI+['imagefsinfo','--output','json'],'R3.IMAGEFS')))
    require(paths == [r['path'] for r in expected['image_filesystems']], 'allocation_imagefs_path_changed')
    for row in (image,opt):
        kind = B.capture(['findmnt','-n','-o','FSTYPE','--target',row['path']],'R3.FILESYSTEM',limit=4096).decode().strip()
        require(kind == 'xfs', 'allocation_reviewed_filesystem_required')
    content = set()
    digests = sorted(plan['blobs'])
    for start in range(0,len(digests),128):
        batch = digests[start:start+128]
        raw = B.capture(B.CTR+['content','ls','--quiet']+['digest=='+d for d in batch], 'R3.CONTENT',timeout=30,limit=65536)
        content.update(parse_content(raw,batch))
    raw = B.capture(B.CTR+['snapshots','--snapshotter','overlayfs','ls'],'R3.SNAPSHOTS',timeout=30,limit=8*1024**2)
    result = budget(plan,content,parse_snapshots(raw,plan['chains']),phase)
    # Refresh free space after bounded cache queries; the report is not a reservation.
    image = stat_row(image_expected['path'],image_expected)
    opt = stat_row('/opt',expected['opt'],sealed_fd)
    total = sum(r['archive_bytes'] for r in profile['images'])
    copies = 2 if phase == 'TARGET_PREPARE' else 1 if phase == 'BEFORE_SEAL' else 0
    image['required_bytes'] = reserve(image)+HEADROOM+result['incremental_peak_allowance_bytes']
    opt['required_bytes'] = reserve(opt)+HEADROOM+copies*total+METADATA
    root['required_bytes'] = 4*GIB+16*1024**2
    result.update(filesystems=[image,opt,root],reserve_bytes=reserve(image),extra_headroom_bytes=HEADROOM,
        storage_budget_pass=True,resource_capacity_pass=False,strict_filesystem_upper_bound=False,
        scope='OBSERVED_TWO_PHASE_ALLOCATION_BUDGET_WITH_LIVE_MONITOR_NOT_A_RESERVATION')
    for row in result['filesystems']:
        if row['available_bytes'] < row['required_bytes'] or row['available_inodes'] < 100000:
            result['storage_budget_pass']=False
            error=ValueError('r3_storage_budget_insufficient')
            error.observation=result
            raise error
    return result
