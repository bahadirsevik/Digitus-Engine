// Plan v4 (export dağıtımı): Ayrı Dışa Aktarım sayfası kaldırıldı — indirmeler
// artık Anahtar Kelimeler + kanal sayfalarının İndir menülerinde. Eski
// bookmark'lar/derin linkler kırılmasın diye route korunur, buraya gelen
// query parametreleri (run_id vb.) taşınarak Keywords'e yönlendirilir
// (repo deseni: Generation.tsx).
import RedirectWithParams from '../components/RedirectWithParams'

export default function Export() {
  return <RedirectWithParams to="/keywords" />
}
