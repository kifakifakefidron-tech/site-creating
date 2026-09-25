# Тестовый YML в формате Тильды (формат описания — как в реальной карточке ЖК Движение)
import random, html
random.seed(7)
zhk = [("РАКУРС","ФМР",45.052,39.02),("ЛУЧШИЙ","ЮМР",45.00,39.07),("СВЕТЛОГРАД","ЗАПАДНЫЙ ОБХОД",45.09,38.91),
       ("САМОЛЕТ 6","П. РОССИЙСКИЙ",45.10,38.97),("АКВАРЕЛИ 2","МУЗЫКАЛЬНЫЙ",45.06,39.05),("ДВИЖЕНИЕ","ЗАПАДНЫЙ ОБХОД",45.088,38.909),
       ("СВОБОДА","ЧМР",45.04,38.95),("ОТРАЖЕНИЕ","ЦМР",45.03,38.98)]
types=["♟СТУДИЯ♟","♟1К♟","♟2К♟","♟3К♟"]
out=['<?xml version="1.0" encoding="UTF-8"?><yml_catalog date="2026-09-24"><shop><name>Стрелы</name><currencies><currency id="RUB" rate="1"/></currencies><categories><category id="1">Квартиры</category></categories><offers>']
for i in range(46):
    z,d,la,lo=random.choice(zhk); t=random.choice(types)
    area={0:random.randint(19,28),1:random.randint(33,45),2:random.randint(50,68),3:random.randint(70,95)}[types.index(t)]
    price=int(area*random.randint(95,140))
    fl=random.randint(1,16); fls=random.choice([9,16,18])
    cond=random.choice(["РЕМ/МЕБ/ТЕХ","РЕМОНТ","ПРЕДЧИСТОВАЯ","БЕЗ РЕМОНТА"])
    desc=f"{t}<br />➳ РАЙОН {d}<br />➳ ЖК {z}<br />➵ ул. Тестовая {i}<br />➵ {area}м2, Этаж: {min(fl,fls)}/{fls}<br />➵ Состояние: {cond}<br />➵ В ДКП: ВСЯ СУММА<br />➵ ОБРЕМЕНЕНИЯ: НЕТ<br />જ⁀➴ Цена - {price:,}".replace(","," ")+"<br />➳ Артём<br />📞 8(961)857-17-72"
    out.append(f'<offer id="{1000+i}" available="true"><url>https://arrowsrealty.ru/tproduct/{100000+i}-zhk-test</url><price>{price}</price><currencyId>RUB</currencyId><categoryId>1</categoryId><picture>https://static.tildacdn.com/stor6466-3362-4266-a132-623139316537/dfc775af9c4237cac25867a49e7e5f27.jpg</picture><name>ЖК {z}</name><description>{html.escape(desc)}</description><param name="Ремонт">{"Да" if "РЕМ" in cond else "Нет"}</param><param name="Год постройки дома">{random.choice([2018,2020,2022,2024])}</param><param name="Материал стен">Монолит/кирпич</param><param name="Жилой комплекс">ЖК {z}</param><param name="Координаты">{la+random.random()/100:.6f}, {lo+random.random()/100:.6f}</param></offer>')
out.append('</offers></shop></yml_catalog>')
open("tests/sample_feed.yml","w").write("".join(out))
