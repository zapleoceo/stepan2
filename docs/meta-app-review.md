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

## Сценарии записи

Поток подключения снимается **один раз** (дубль А) — он закрывает четыре разрешения, — затем
два коротких дубля с сообщениями. Потом нарезать по копии на разрешение, каждой дать своё
описание.

### Дубль А — подключение бизнеса (экран согласия)

Закрывает `pages_show_list`, `pages_read_engagement`, `pages_manage_metadata`,
`business_management`.

1. Открыть `https://stepan2.zapleo.com`, войти, перейти к коннектору демо-филиала.
2. Нажать **Connect Facebook**. Диалог согласия Меты должен быть виден целиком — именно этот
   экран Мета и хочет увидеть.
3. Показать список запрашиваемых разрешений на диалоге, продолжить, выбрать страницу
   **Zapleo Soft**, подтвердить.
4. Вернуться на страницу коннектора, где видно, что страница подключена.

### Дубль Б — ответ в Messenger

Закрывает `pages_messaging`.

1. Со второго аккаунта написать странице Zapleo Soft в Messenger.
2. Показать, как сообщение приходит во входящие Степана.
3. Показать, как ответ уходит и появляется в Messenger на стороне отправителя.

### Дубль В — ответ в Instagram Direct

Закрывает `instagram_basic`, `instagram_manage_messages`.

1. Со второго аккаунта написать в Direct на `@zapleosoft`.
2. Показать, как сообщение приходит в те же входящие, со значком Instagram.
3. Показать доставленный ответ в Direct.

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
