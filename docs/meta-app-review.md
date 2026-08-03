# Проверка приложения в Meta — пакет для подачи

Приложение: **Stepan by Zapleo** · ID `2315128069295857` · Бизнес `237014350216399`
Домен `stepan2.zapleo.com` · демо-филиал **TEST (7)**, канал 16, страница *Zapleo Soft*.

Старый раздел `/app-review/permissions/` Мета убрала — этот адрес теперь редиректит.
Всё подаётся из **Сценарии использования → Настроить → Разрешения → Действия →
«Добавить в запрос на проверку приложения»**, по каждому разрешению отдельно.

## Состояние на 02.08.2026

| Пункт | Состояние |
|---|---|
| Верификация бизнеса | пройдена |
| Иконка, название, категория «Обмен сообщениями» | заполнены |
| Privacy / Terms / Data deletion | опубликованы, отвечают 200 |
| Домен приложения `zapleo.com` | задан |
| Требуемые действия | нет |
| Сценарии использования | Messenger + Instagram, оба настроены |
| Разрешения с живыми вызовами API | 7, все «Готово к тестированию» |
| Режим приложения | **не опубликовано** |
| Запрос на проверку | **не создан** |
| Скринкасты | **не записаны** |

Вызовы, которые Мета уже видит: `business_management`, `pages_messaging`,
`pages_read_engagement`, `pages_show_list`, `pages_manage_metadata` — по 5.8k;
`instagram_basic`, `instagram_manage_messages` — по 2.9k. Засчитываются вызовы **не старше
30 дней**, на дату этого файла они свежие — значит подавать без долгой паузы.

## Единственное жёсткое ограничение по записи

Мета требует, чтобы **курсор был виден** в каждом скринкасте. Клод записать их не может:
инструмент, который водит настоящий браузер, кликает через DOM и физический курсор не
двигает, а инструмент, двигающий настоящий курсор, работает с браузерами только на чтение.
Поэтому записывает человек. Клод готовит сценарии, следит по логам во время каждого дубля и
подтверждает, что вызов действительно ушёл.

Формат: 1080p, ширина ≤1440 px, **отдельное видео на каждое разрешение**, без тестовых
логинов в кадре, интерфейс лучше английский.

## Коннектор перед записью: отключать не нужно

Канал 16 подключён, но **не через этот поток**: он работает на системном токене из настройки
`meta_system_user_token`, а `channel_session` пуст — кнопку «Connect Facebook» для него ни
разу не нажимали. Значит Facebook покажет экран согласия целиком, как новому клиенту. Ломать
и пересоздавать коннектор ради записи не надо.

Что произойдёт в конце дубля А: `_persist` записывает `channel_session`, а воркер отдаёт ему
приоритет над системным токеном (`wiring.py`) — канал переедет на Page-токен, полученный в
кадре. Для филиала 7 это безопасно: Page-токена хватает и на Messenger, и на Direct, а
рекламных прав в наших scope нет и рекламный аккаунт (`fb_account_id`) к этому каналу не
привязан.

**Откат в одну строку**, если после записи что-то поведёт себя не так:

```sql
DELETE FROM channel_session WHERE channel_id = 16;
```

Канал тут же вернётся на системный токен — он никуда не девается, `_persist` его не трогает.

## Подготовка — один раз перед всеми дублями

1. **Язык интерфейса — английский.** Открыть `https://stepan2.zapleo.com/lang/en`. Заявку
   читает англоязычный рецензент; русский интерфейс в кадре — лишний повод переспросить.
2. **Окно браузера ≤1440 px по ширине**, запись 1080p. Курсор в кадре обязателен.
3. **Закрыть всё лишнее**: другие вкладки, мессенджеры, уведомления. В кадре не должно быть
   ничего, кроме продукта и Меты.
4. **Не разворачивать блок с ручным токеном** на странице коннектора (он спрятан под
   «Advanced»/`<details>`). Там на экран попадёт системный токен — это утечка секрета в видео,
   которое уходит третьей стороне.
5. Второй аккаунт (тот, что играет клиента) — заранее залогинен в другом браузере или на
   телефоне, чтобы не снимать ввод пароля.
6. Проверить, что бот включён: филиал TEST, `agent_enabled` и `sending_enabled` — оба on.

## Сценарии записи

Поток подключения снимается **один раз** (дубль А) — он закрывает четыре разрешения, — затем
два коротких дубля с сообщениями. Потом нарезать по копии на разрешение, каждой дать своё
описание.

### Дубль А — подключение бизнеса (экран согласия)

Закрывает `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`,
`business_management`. Длительность ~40–60 секунд.

1. **Начать запись на странице входа** `https://stepan2.zapleo.com` — рецензент должен видеть,
   что это продукт с учётными записями, а не голая страница.
2. Войти. Попасть в панель. Задержаться на секунду, чтобы было видно интерфейс.
3. Перейти на страницу каналов демо-филиала: **Channels → TEST → канал Meta Business**,
   адрес `https://stepan2.zapleo.com/channels/16/credential`.
4. **Навести курсор на кнопку `Connect Facebook` и на секунду задержать** — рецензент должен
   понять, что дальше действие пользователя, а не автоматика.
5. Нажать. Открывается диалог Меты.
6. **Главный кадр всей заявки:** экран согласия. Не торопиться — **держать 3–5 секунд**,
   чтобы список запрашиваемых разрешений успел прочитаться. Если список свёрнут — раскрыть.
7. Продолжить. На шаге выбора страницы — выбрать **Zapleo Soft**, показать, что выбор делает
   пользователь, а не приложение за него.
8. Подтвердить. Дождаться возврата на нашу страницу с надписью, что страница подключена.
9. **Задержаться на итоговом экране 2–3 секунды** и остановить запись.

### Дубль Б — ответ в Messenger

Закрывает `pages_messaging`. Длительность ~30–40 секунд.

1. Экран поделить: слева Messenger второго аккаунта, справа входящие Степана
   (`https://stepan2.zapleo.com/inbox`). Если делить неудобно — снимать переключением вкладок,
   но тогда медленно.
2. Со второго аккаунта написать странице Zapleo Soft **осмысленный вопрос**, например
   *"Hi, how much does it cost for one business?"*. Не «hi» — на голое приветствие отвечает
   шаблон, а не модель, и в кадре продукт выглядит беднее, чем он есть.
3. Показать, как сообщение появляется во входящих Степана.
4. Дождаться ответа и показать его **в самом Messenger у отправителя** — то есть что оно
   реально доставлено, а не просто нарисовано в нашей панели.

### Дубль В — ответ в Instagram Direct

Закрывает `instagram_basic`, `instagram_manage_messages`. Длительность ~30–40 секунд.

1. Так же, но со второго аккаунта в Instagram Direct на `@zapleosoft`.
2. Написать осмысленный вопрос — можно на другом языке, это хорошо смотрится: Степан отвечает
   на языке клиента.
3. Показать сообщение во входящих **со значком Instagram** — видно, что мы различаем каналы.
4. Показать доставленный ответ в самом Direct.

## Чего не делать

- Не показывать в кадре токены, секреты, `.env`, содержимое блока «Advanced».
- Не использовать тестовые/служебные учётки Меты — рецензент должен видеть обычный аккаунт.
- Не ускорять видео и не вырезать середину действия: путь должен читаться целиком.
- Не подставлять одно и то же описание нескольким разрешениям.
- Не тянуть с подачей: вызовы API засчитываются за последние 30 дней.

## Описания разрешений

По одному на разрешение. **Текст оставлен на английском намеренно — его копировать в форму
как есть**: Мета проверяет заявки на английском, а одинаковый текст на нескольких разрешениях
— типовая причина отказа. Под каждым — пояснение по-русски, о чём оно.

**pages_show_list** — *показываем владельцу список его страниц, чтобы он выбрал одну для
подключения.*
> After a business owner grants access, we show them the list of Facebook Pages they manage so
> they can choose which single Page to connect to Stepan. Without it we cannot present that
> choice and the owner could not tell us which Page the assistant should answer for. We store
> only the id and name of the Page they pick.

**pages_messaging** — *основная функция: читаем входящие и отвечаем в Messenger.*
> Stepan replies to people who message the connected Page on Messenger. We read the incoming
> conversation to understand the question and send one reply back inside the standard messaging
> window. This is the core function of the product: the business is buying an assistant that
> answers its customers, which is impossible without sending and receiving Page messages.

**pages_read_engagement** — *читаем профиль страницы и личность написавшего, чтобы отвечать
конкретному человеку, а не в пустоту.*
> We read the connected Page's own profile and the identity of the person writing in, so a
> conversation is attributed to the right customer and the reply is addressed to them by name.
> Without it every conversation would be anonymous and our inbox could not tell two customers
> apart.

**pages_manage_metadata** — *подписываем страницу на вебхуки, чтобы сообщение приходило сразу,
а не через опрос.*
> We subscribe the connected Page to message webhooks so that an incoming customer message
> reaches us immediately. Without the subscription we would have to poll continuously, which is
> slower for the customer and heavier on Meta's infrastructure.

**instagram_basic** — *читаем id и username привязанного IG-аккаунта, чтобы отличать себя от
клиента в переписке.*
> We read the Instagram professional account linked to the connected Page: its id and username.
> This tells us which participant in a Direct thread is the business itself and which is the
> customer, so we reply to the customer rather than to our own account.

**instagram_manage_messages** — *то же, что в Messenger, но в Direct — канал, которым
пользуется большинство клиентов.*
> Stepan reads and answers messages the business receives in Instagram Direct — the same
> assistant function as on Messenger, on the channel most of our customers actually use.
> Without it the product cannot serve Instagram businesses at all.

**business_management** — *определяем, какому бизнесу принадлежит страница, и получаем
привязанный к нему токен; на этом же держится разделение данных клиентов.*
> We resolve which Business the connected Page belongs to and obtain the Page access token
> scoped to it, which is how Meta requires an app to act for a business asset. It is also how we
> keep one customer's data strictly separated from another's.

## Порядок действий

1. Записать дубли А, Б, В (за клавиатурой человек, Клод сверяет каждый вызов по логам).
2. Добавить все 7 разрешений в запрос на проверку.
3. Вставить соответствующее описание и загрузить соответствующее видео.
4. Отправить. Публикация приложения — отдельный переключатель, уже после одобрения.
