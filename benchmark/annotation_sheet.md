# Faz D etiketleme sayfası (KÖR)

Tanımlar için annotation_guide.md'ye bakın. HER hücre doldurulmalı.
'skip' YALNIZ bozuk/anlaşılamayan metin içindir. Çift sütununda ✓
olan maddelerin ikinci etiketi AYRI dosyada bağımsız doldurulur
(annotation_sheet_second.md).

| item | Çift | Kelime | intent | GT | GA | ADS | opin | merak | gündem | rel |
|------|------|--------|--------|----|----|-----|------|-------|--------|-----|
| K001 |  | bilanço okuma rehberi | commercial | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K002 |  | borsa canlı yayın | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K003 |  | hisse senedi hediye eden bankalar | commercial | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K004 |  | bist anlık veri | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K005 | ✓ | borsa istanbul hacim | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K006 |  | borsa al | commercial | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K007 |  | fırsat hisseler | informational | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K008 |  | borsa dolandırıcılığı şikayet | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K009 |  | borsa tüyo | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K010 | ✓ | hisse takip programı | commercial | 1 | 1 | 2 | 1 | 0 | 0 | high |
| K011 |  | matriks iq tarama | navigational | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K012 |  | borsa simülasyon oyunu | commercial | 1 | 1 | 1 | 0 | 0 | 0 | mid |
| K013 |  | hisse uygulamaları | commercial | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K014 |  | yukselecek hisseler | informational | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K015 | ✓ | hisse canli | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K016 |  | alınacak hisse | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K017 |  | kap resmi sitesi | navigational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K018 |  | al sat yapılacak hisseler | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K019 |  | borsa canlı | commercial | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K020 | ✓ | en hızlı hisse senedi alım satım | commercial | 0 | 1 | -1 | 0 | 0 | 0 | low |
| K021 |  | komisyonsuz borsa uygulaması | commercial | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K022 |  | temel teknik analiz | informational | 1 | 1 | 1 | 1 | 0 | 0 | mid |
| K023 |  | anlık hisse senedi takip programı | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K024 |  | borsa takas verileri | informational | 1 | 1 | -1 | 0 | 0 | 0 | low |
| K025 | ✓ | temel analiz teknik analiz | informational | 1 | 1 | 1 | 1 | 0 | 0 | mid |
| K026 |  | orta uzun vade hisse önerileri | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K027 |  | hisse hareketleri | informational | 1 | 1 | 1 | 0 | 0 | 0 | low |
| K028 |  | spk lisans sorgulama | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K029 |  | hisse al sat | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K030 | ✓ | en karlı hisse senedi | informational | 1 | 1 | 1 | 0 | 0 | 0 | mid |
| K031 |  | hisse satın alma | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K032 |  | vadeli mevduat oranları | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K033 |  | bist veri | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K034 |  | anlık grafik borsa | commercial | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K035 | ✓ | borsa ekranı | commercial | 1 | 1 | 1 | 0 | 0 | 0 | mid |
| K036 |  | hisse alım | informational | 0 | 1 | -1 | 0 | 0 | 0 | low |
| K037 |  | info günlük bülten | navigational | 0 | 1 | -1 | 0 | 0 | 0 | low |
| K038 |  | bedava hisse | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K039 |  | hisse senedi nedir kısaca ödev | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K040 | ✓ | mantıklı yatırım tavsiyeleri | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K041 |  | borsa işlem hacmi | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K042 |  | uzun vade yatırım tavsiyesi | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K043 |  | tradingview hisse tarama | commercial | 1 | 1 | 1 | 1 | 0 | 0 | high |
| K044 |  | güncel hisse senedi önerileri | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K045 | ✓ | güçlü al veren hisseler | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K046 |  | bist invest | informational | 0 | 1 | -1 | 0 | 0 | 0 | low |
| K047 |  | borsa takip sistemi | commercial | 1 | 1 | 1 | 1 | 0 | 0 | high |
| K048 |  | bist işlem gören şirketler | informational | 0 | 1 | -1 | 0 | 0 | 0 | high |
| K049 |  | bist hisse listesi | informational | 0 | 1 | -1 | 0 | 0 | 0 | high |
| K050 | ✓ | bist yatırım | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K051 |  | borsa analiz uygulaması satın al | transactional | 1 | 1 | 2 | 0 | 0 | 0 | high |
| K052 |  | info yatırım hisse | informational | 0 | 0 | -1 | 0 | 0 | 0 | mid |
| K053 |  | borsa terminali abonelik | transactional | 1 | 1 | 2 | 0 | 0 | 0 | high |
| K054 |  | piyasa takip ekranı | commercial | 1 | 1 | 1 | 0 | 0 | 0 | mid |
| K055 | ✓ | hisse takip uygulaması öneri | commercial | 1 | 1 | 2 | 0 | 0 | 0 | high |
| K056 |  | hisse alım satım | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K057 |  | hisse senedi anlık takip | commercial | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K058 |  | hisse senedi en ucuz | informational | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K059 |  | borsa göstergeleri | informational | 1 | 1 | 1 | 1 | 0 | 0 | high |
| K060 | ✓ | portföy takip uygulaması karşılaştırma | commercial | 1 | 1 | 2 | 1 | 0 | 0 | high |
| K061 |  | teknik analiz programı premium | commercial | 1 | 1 | 2 | 0 | 0 | 0 | high |
| K062 |  | günlük borsa haberleri | informational | 1 | 1 | -1 | 1 | 1 | 0 | high |
| K063 |  | faiz kararı sonrası borsa | trend_worthy | 0 | 0 | -1 | 0 | 0 | 1 | low |
| K064 |  | borsa hisse önerileri | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K065 | ✓ | sanal borsa oyunu | commercial | 1 | 1 | 1 | 0 | 0 | 0 | mid |
| K066 |  | borsa piyasalar | informational | 0 | 1 | -1 | 0 | 0 | 0 | low |
| K067 |  | en iyi borsa analiz uygulaması hangisi | commercial | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K068 |  | hisse net | navigational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K069 |  | bilanço programı | commercial | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K070 | ✓ | emeklilik fonu getirileri | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K071 |  | tradingview türkçe giriş | navigational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K072 |  | borsa öğrenme uygulaması | commercial | 1 | 1 | -1 | 0 | 0 | 0 | low |
| K073 |  | online borsa | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K074 |  | hisse teknik analiz | informational | 1 | 1 | -1 | 1 | 0 | 0 | high |
| K075 | ✓ | anlık hisse | commercial | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K076 |  | loğo hisse | skip | skip | skip | skip | skip | skip | skip | skip |
| K077 |  | bist100 toplam değeri | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K078 |  | online borsa takip | commercial | 1 | 1 | 1 | 0 | 0 | 0 | mid |
| K079 |  | logo hisse | trend_worthy | 0 | 1 | -1 | 0 | 0 | 0 | high |
| K080 | ✓ | borsa çöküşü haberleri | trend_worthy | 0 | 0 | -1 | 0 | 0 | 1 | mid |
| K081 |  | en düşük hisseler | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K082 |  | borsa sitesi | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K083 |  | en iyi kripto borsası | informational | 0 | 0 | -1 | 0 | 0 | 0 | mid |
| K084 |  | bist en çok yükselen | informational | 1 | 1 | -1 | 1 | 1 | 0 | high |
| K085 | ✓ | borsa hisse yorumları | informational | 1 | 1 | 1 | 1 | 0 | 0 | high |
| K086 |  | rsi göstergesi nedir | informational | 0 | 1 | -1 | 0 | 0 | 0 | low |
| K087 |  | dolar kuru bugün | trend_worthy | 0 | 0 | -1 | 0 | 0 | 1 | mid |
| K088 |  | ücretsiz hisse takip uygulaması | commercial | 0 | 1 | -1 | 0 | 0 | 0 | high |
| K089 |  | borsa al sat | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K090 | ✓ | hisse senedi uzun vadeli yatırım | informational | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K091 |  | hisse derinlik | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K092 |  | uygun hisseler | informational | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K093 |  | bist iletisim hisseleri | informational | 0 | 1 | -1 | 0 | 0 | 0 | high |
| K094 |  | teknik analiz öğrenme | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K095 | ✓ | hisse senedi tarama | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K096 |  | borsa uygulaması pro sürüm | commercial | 1 | 1 | 2 | 0 | 0 | 0 | high |
| K097 |  | borsa bist 100 hisseleri | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K098 |  | hisse takip programı indir | transactional | 1 | 1 | 2 | 0 | 0 | 0 | high |
| K099 |  | endeks yorum | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K100 | ✓ | matriks borsa crack | navigational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K101 |  | info borsa | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K102 |  | günlük hisse tarama | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K103 |  | kısa hisse | informational | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K104 |  | hisse analiz siteleri | commercial | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K105 | ✓ | borsa filmi izle | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K106 |  | günlük al sat yapılabilecek hisseler | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K107 |  | en düşük hisse senetleri | informational | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K108 |  | hisse fikirleri | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K109 |  | borsa istanbul canlı veri | commercial | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K110 | ✓ | bist mali | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K111 |  | türk hisse senedi piyasası | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K112 |  | borsa 50 | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K113 |  | app hisse | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K114 |  | en çok kazandıran yatırım aracı | informational | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K115 | ✓ | borsa günlük al sat | informational | 0 | 0 | -1 | 1 | 1 | 0 | low |
| K116 |  | hissefy giriş | navigational | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K117 |  | hisse senetleri hakkında bilgi | informational | 0 | 1 | 1 | 0 | 0 | 0 | high |
| K118 |  | borsa hisse grafikleri | commercial | 0 | 1 | 1 | 0 | 0 | 0 | mid |
| K119 |  | artacak hisseler | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K120 | ✓ | en ucuz hisse senetleri | commercial | 1 | 1 | 1 | 1 | 1 | 0 | high |
| K121 |  | bist resmi site | navigational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K122 |  | hisse tarama aracı üyelik | transactional | 1 | 1 | 2 | 0 | 0 | 0 | high |
| K123 |  | bist canlı veri | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K124 |  | coin yorumlari | informational | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K125 | ✓ | matriks veri terminali crack | navigational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K126 |  | finans hisseleri | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K127 |  | bist 100 direnç destek | informational | 0 | 1 | 1 | 0 | 0 | 0 | high |
| K128 |  | hissefy indir | navigational | 1 | 1 | 2 | 0 | 0 | 0 | high |
| K129 |  | borsa analiz programı tavsiye | navigational | 1 | 1 | 2 | 0 | 0 | 0 | high |
| K130 | ✓ | borsada yatırım şirketleri | informational | 0 | 1 | -1 | 0 | 0 | 0 | high |
| K131 |  | anlık hisse takibi | commercial | 1 | 1 | -1 | 0 | 0 | 0 | high |
| K132 |  | borsa kağıt hisseleri | informational | 1 | 1 | -1 | 0 | 0 | 0 | high |
| K133 |  | en iyi borsa | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K134 |  | mrk hisse | trend_worthy | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K135 | ✓ | canlı borsa alım satım | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K136 |  | türkiye borsası canlı | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K137 |  | borsa istanbul endeks canlı | informational | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K138 |  | hisse senedi analiz | informational | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K139 |  | en yüksek hisse senedi | informational | 1 | 1 | 1 | 0 | 0 | 0 | high |
| K140 | ✓ | bist de işlem gören şirketler | informational | 0 | 1 | -1 | 0 | 0 | 0 | high |
| K141 |  | konut kredisi hesaplama | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K142 |  | altın yatırımı mantıklı mı | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K143 |  | hisse raporları | commercial | 1 | 1 | 1 | 0 | 0 | 1 | high |
| K144 |  | macd tarama formülü | informational | 0 | 0 | -1 | 0 | 0 | 0 | mid |
| K145 | ✓ | canli bist | commercial | 1 | 1 | -1 | 0 | 0 | 0 | mid |
| K146 |  | borsa bilgileri | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K147 |  | iş hisse | trend_worthy | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K148 |  | iş a hisse | trend_worthy | 0 | 1 | -1 | 0 | 0 | 0 | mid |
| K149 |  | hisse işlemleri | informational | 0 | 0 | -1 | 0 | 0 | 0 | low |
| K150 | ✓ | borsa finans | informational | 0 | 1 | -1 | 0 | 0 | 0 | mid |
