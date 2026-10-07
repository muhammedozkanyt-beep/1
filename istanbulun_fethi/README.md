# İstanbul'un Fethi: 1453 — Matriks Tarih

2D animasyonlu belgesel videosu (≈ 9 dk 40 sn, 1280×720, Türkçe seslendirme + altyazı).

- `cikti/Istanbulun_Fethi_1453_MatriksTarih.mp4`: hazır video
- `cikti/Istanbulun_Fethi_1453.srt`: altyazı dosyası
- `senaryo.py`: 18 sahnelik senaryo
- `DubVoice_metni.txt`: DubVoice'a yapıştırılacak sahne sahne metin
- `render.py`: videoyu üreten program

## Kendi logonuz, DubVoice sesi ve Flow Music müziğiyle yeniden üretmek

1. Kanal logosunu bu klasöre `logo.png` adıyla koyun.
2. `DubVoice_metni.txt` içindeki her sahneyi DubVoice'ta seslendirin ve `ses/01.mp3` … `ses/18.mp3` olarak kaydedin.
3. Flow Music'ten aldığınız fon müziğini `muzik.mp3` adıyla koyun.
4. `python3 render.py` komutunu çalıştırın. Gereksinimler: Python 3, Pillow, numpy, ffmpeg, espeak-ng.

Sahne süreleri ses dosyalarının uzunluğuna göre otomatik ayarlanır. Eksik ses dosyası olan sahnelerde geçici espeak-ng sesi kullanılır.
