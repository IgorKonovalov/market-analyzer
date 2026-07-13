/**
 * Russian catalog (Plan 0069 phase 6, ADR-0063).
 *
 * Mirrors every key in `en` — enforced two ways: `satisfies Record<keyof typeof
 * en, string>` fails the typecheck on a missing or extra key, and
 * `locales/parity.test.ts` re-checks the key sets at runtime (both directions).
 *
 * Placeholders (`{param}`) and the ICU-lite `{count, plural, …}` forms are
 * preserved verbatim — only the surrounding words change. Russian needs three
 * count categories, so plural arms carry `one`/`few`/`many`/`other` (English
 * had only `one`/`other`); `Intl.PluralRules('ru')` in `t()` selects the arm.
 * Numbers/dates/currency stay `en-US` by decision (ADR-0063), so `#` and numeric
 * `{param}` values still format `en-US` here.
 *
 * Accepted English residue (NOT translated — the documented ADR-0063 seam):
 * external news headlines, symbol names, dynamic `str(exc)` data-layer errors,
 * the forecast `fallback_reason` composed diagnostic, and raw upstream
 * classification text. Everything closed-vocabulary IS translated (below).
 */
import type { Catalog } from '../lib/i18n'
import type { en } from './en'

export const ru = {
  // ── App shell: nav + backtest panel (App.tsx) ──
  'app.nav.primaryLabel': 'Основные',
  'app.nav.chart': 'График',
  'app.nav.backtests': 'Бэктесты',
  'app.nav.signals': 'Сигналы',
  'app.nav.recommendations': 'Рекомендации',
  'app.nav.technicalRead': 'Технический разбор',
  'app.nav.trackRecord': 'История точности',
  'app.nav.forecast': 'Прогноз',
  'app.nav.convergence': 'Сходимость',
  'app.nav.defi': 'DeFi',
  'app.nav.news': 'Новости',
  'app.nav.alerts': 'Оповещения',
  'app.nav.settings': 'Настройки',
  'app.backtest.loading': 'Загрузка результата бэктеста…',
  'app.backtest.loadError': 'Не удалось загрузить результат бэктеста:',
  'app.backtest.backToRecent': 'Назад к недавним бэктестам',
  'app.backtest.noneSelected': 'Бэктест не выбран. Откройте «Недавние бэктесты», чтобы выбрать.',
  'app.backtest.recentBacktests': 'Недавние бэктесты',

  // ── Backtest result view (BacktestView.tsx) ──
  'backtest.rootLabel': 'Бэктест {runId}',
  'backtest.backButton': '← Недавние бэктесты',
  'backtest.engineVersion': 'движок v{version}',
  'backtest.metricsHeading': 'Метрики',
  'backtest.totalReturn': 'Общая доходность',
  'backtest.sharpe': 'Шарп',
  'backtest.maxDrawdown': 'Макс. просадка',
  'backtest.maxDdDuration': 'Длит. макс. просадки',
  'backtest.winRate': 'Доля прибыльных',
  'backtest.tradeCount': 'Число сделок',
  'backtest.buyAndHold': 'Купи и держи',
  'backtest.equityCurveHeading': 'Кривая капитала',
  'backtest.tradeLogHeading': 'Журнал сделок ({count})',
  'backtest.noTrades': 'В этом прогоне сделок нет.',
  'backtest.colEntry': 'Вход',
  'backtest.colExit': 'Выход',
  'backtest.colEntryPrice': 'Цена входа $',
  'backtest.colExitPrice': 'Цена выхода $',
  'backtest.colPnlUsd': 'P&L $',
  'backtest.colPnlPct': 'P&L %',
  'backtest.colStatus': 'Статус',
  'backtest.statusOpen': 'Открыта',
  'backtest.statusClosed': 'Закрыта',
  'backtest.equityCurveLabel': 'Кривая капитала для {symbol} {timeframe}, {points} точек',

  // ── Alerts view (AlertsView.tsx) ──
  'alerts.alerts': 'Оповещения',
  'alerts.watches': 'Наблюдения',
  'alerts.alertHistory': 'История оповещений',
  'alerts.disclaimer':
    'Оповещения сообщают об условиях, за которыми агент следит по запросу, — это факты, а не советы.',
  'alerts.loadingWatches': 'Загрузка наблюдений…',
  'alerts.watchesError': 'Не удалось загрузить наблюдения:',
  'alerts.noWatches': 'Пока нет наблюдений — попросите агента создать одно.',
  'alerts.watchRowLabel': 'Наблюдение {id}: {symbol} {timeframe} {kind} — {state}',
  'alerts.enabled': 'включено',
  'alerts.disabled': 'выключено',
  'alerts.loadingHistory': 'Загрузка истории оповещений…',
  'alerts.historyError': 'Не удалось загрузить историю оповещений:',
  'alerts.nothingFired': 'Пока ничего не сработало.',
  'alerts.watchFallback': 'наблюдение {id}',
  'alerts.noConditionText': '(нет текста условия)',
  'alerts.kind.indicatorThreshold': 'порог индикатора',
  'alerts.kind.pattern': 'паттерн',
  'alerts.kind.strategySignal': 'сигнал стратегии',

  // ── Toast (Toast.tsx) ──
  'toast.dismiss': 'Закрыть уведомление',

  // ── OHLCV chart view (OhlcvView.tsx) ──
  'ohlcv.viewLabel': 'Представление OHLCV для {symbol} {timeframe}',
  'ohlcv.refresh': 'Обновить',
  'ohlcv.refreshing': 'Обновление…',
  'ohlcv.updated': 'Обновлено ✓',
  'ohlcv.backfillingLabel': 'Догрузка {symbol} {timeframe}',
  'ohlcv.backfilling': 'Догрузка…',
  'ohlcv.loadingChart': 'Загрузка графика',
  'ohlcv.loadingBars': 'Загрузка {symbol} {timeframe}…',
  'ohlcv.loadFailedPrefix': 'Не удалось загрузить',
  'ohlcv.retry': 'Повторить',
  'ohlcv.emptyBars': 'Нет баров для {symbol} {timeframe} в этом окне.',
  'ohlcv.chartLabel': 'Свечной график для {symbol} {timeframe}, {count} баров',
  'ohlcv.loadingOlder': 'Загрузка более ранних баров',
  'ohlcv.loadingHistory': 'Загрузка истории…',
  'ohlcv.olderBarsError': 'Не удалось загрузить более ранние бары:',
  'ohlcv.historyClampedNotice':
    'показана максимально доступная история (~{days} дн.) для {timeframe}',
  'ohlcv.currentPriceLabel': 'Текущая цена для {symbol}',
  'ohlcv.disconnectedLabel':
    'Поток цены для {symbol} отключён — показано последнее известное значение',
  'ohlcv.disconnected': 'отключено',

  // ── Symbol picker (SymbolPicker.tsx) ──
  'symbolPicker.symbol': 'Символ',
  'symbolPicker.timeframe': 'Таймфрейм',
  'symbolPicker.symbolSuggestions': 'Подсказки символов',
  'symbolPicker.deepUsdHint': 'глубокий USD',

  // ── Agent-mode toggle (AgentModeToggle.tsx) ──
  'agentMode.toggle': 'Переключить режим агента',
  'agentMode.label': 'Режим агента',
  'agentMode.on': 'ВКЛ',
  'agentMode.off': 'ВЫКЛ',

  // ── Recent backtests list (RecentBacktestsView.tsx) ──
  'recent.title': 'Недавние бэктесты',
  'recent.lede':
    'Сохранённые прогоны из `runs/`. Нажмите на строку, чтобы открыть полный результат.',
  'recent.loading': 'Загрузка прогонов…',
  'recent.loadError': 'не удалось загрузить бэктесты',
  'recent.empty':
    'Пока нет бэктестов. Попросите Claude запустить один — например, «протестируй RSI на AAPL, дневной таймфрейм, за последний год».',
  'recent.colStrategy': 'Стратегия',
  'recent.colSymbol': 'Символ',
  'recent.colTimeframe': 'Таймфрейм',
  'recent.colRange': 'Диапазон',
  'recent.colTotalReturn': 'Общая доходность',
  'recent.colSharpe': 'Шарп',
  'recent.colMaxDd': 'Макс. просадка',
  'recent.colTrades': 'Сделки',
  'recent.colFinished': 'Завершён',
  'recent.openBacktestLabel': 'Открыть бэктест {runId}',

  // ── News view (NewsView.tsx) ──
  'news.title': 'Новости',
  'news.lede':
    'Свежие заголовки и совокупная тональность. Оставьте символ пустым, чтобы просмотреть все ленты.',
  'news.symbolLabel': 'Символ',
  'news.symbolPlaceholder': 'например BTC (пусто = все ленты)',
  'news.windowLabel': 'Окно',
  'news.load': 'Загрузить',
  'news.loading': 'Загрузка новостей…',
  'news.loadError': 'не удалось загрузить новости',
  'news.empty': 'Нет заголовков в этом окне.',
  'news.headlinesLabel': 'Заголовки',
  'news.toneScore': 'тон {score}',
  'news.toneCounts': '{pos} полож. / {neg} отриц. / {neu} нейтр.',

  // ── DeFi wallet-P&L view (DefiPnlView.tsx) ──
  'defi.title': 'P&L кошелька',
  'defi.lede':
    'Вставьте адрес кошелька, чтобы реконструировать его DeFi P&L — сначала LP-позиции, с реализованными значениями за 7/30/90 дней и за всё время.',
  'defi.addressLabel': 'Адрес кошелька',
  'defi.addressPlaceholder': '0x…',
  'defi.refreshLabel': 'Обновить из источника (медленнее)',
  'defi.analyze': 'Анализ',
  'defi.invalidAddress': 'Введите корректный адрес 0x… (40 hex-символов).',
  'defi.recentLabel': 'Недавние',
  'defi.idle': 'Вставьте адрес кошелька и нажмите «Анализ».',
  'defi.loading': 'Реконструкция P&L кошелька…',
  'defi.empty': 'Позиции для этого кошелька не найдены.',
  'defi.partialBanner': 'Частично — {excluded} из {total} позиций исключено (не удалось оценить).',
  'defi.summary.realized': 'Реализованный P&L',
  'defi.summary.unrealized': 'Нереализованный P&L',
  'defi.summary.complete': 'Полные позиции',
  'defi.summary.completeValue': '{complete} из {total}',
  'defi.legend':
    'Жирные числа — реализованный P&L, прибыль, зафиксированная ончейн-событиями. Подстрока в скобках — оценочная полная доходность, включая нереализованный дрейф.',
  'defi.explorerLink': 'Открыть в {explorer}',
  'defi.explorerWalletTitle': 'Открыть этот кошелёк в {explorer} в браузере',
  'defi.poolLinkTitle': 'Открыть контракт пула в {explorer}',
  'defi.copyId': 'Скопировать полный ID позиции',
  'defi.copied': 'Скопировано',
  'defi.tableLabel': 'LP-позиции',
  'defi.col.position': 'Позиция',
  'defi.col.unclaimed': 'Не получено',
  'defi.estReturnLabel': 'оцен. доходность',
  'defi.otherLabel': 'Прочие позиции',
  'defi.otherFigures': 'реализовано {realized} · нереализовано {unrealized}',
  'defi.incompleteGeneric': 'неполные данные — не удалось оценить',
  'defi.error.setKeyHint':
    'Источник данных не настроен — укажите ключ Zerion API в Настройках, чтобы анализировать кошельки.',
  'defi.error.agentModeOff': 'Режим агента выключен — включите его в Настройках.',

  // ── Live-signal view (LiveSignalView.tsx) ──
  'signals.liveSignalEvaluation': 'Оценка сигнала в реальном времени',
  'signals.noEvaluation': 'Оценки пока нет — попросите агента оценить стратегию.',
  'signals.currentPosition': 'Текущая позиция',
  'signals.lastSignal': 'Последний сигнал',
  'signals.noneYet': 'пока нет',
  'signals.freshness': 'Свежесть',
  'signals.freshFired': 'свежий — сработал на последнем закрытом баре',
  'signals.noFreshSignal': 'нет свежего сигнала на последнем закрытом баре',
  'signals.evaluatedThrough': 'Оценено до',
  'signals.closedBars': '{count} закрытых баров',
  'signals.formingNote':
    'Последний бар ещё формируется и был исключён — данные читаются по последний закрытый бар.',
  'signals.disclaimer': 'Отчёт о текущем состоянии сигнала стратегии — не совет.',
  'signals.at': 'в',
  'signals.barsAgo':
    ' ({count, plural, one {# бар} few {# бара} many {# баров} other {# бара}} назад)',
  'signals.kind.enterLong': 'вход в лонг',
  'signals.kind.exitLong': 'выход из лонга',
  'signals.kind.enterShort': 'вход в шорт',
  'signals.kind.exitShort': 'выход из шорта',

  // ── Candlestick chart controls (CandlestickChart.tsx) ──
  'chart.selectingRange': 'Выбор диапазона… (Esc для отмены)',
  'chart.selectRange': 'Выбрать диапазон',
  'chart.scanning': 'Сканирование…',
  'chart.candlesticks': 'Свечи',
  'chart.chartPatterns': 'Графические паттерны',
  'chart.patternCount':
    '{count, plural, one {# паттерн} few {# паттерна} many {# паттернов} other {# паттерна}}',
  'chart.noPatternsInView': 'Нет паттернов в области',
  'chart.noChartPatternsInView': 'Нет графических паттернов в области',
  'chart.ariaLabel': 'Свечной график, {count} баров',
  // Market-structure badge (MarketStructureBadge.tsx, Plan 0092 / ADR-0084).
  'chart.structure.label': 'Структура',
  'chart.structure.trend.up': 'Вверх',
  'chart.structure.trend.down': 'Вниз',
  'chart.structure.trend.range': 'Диапазон',

  // ── Forecast view (ForecastView.tsx) chrome ──
  'forecast.panelLabel': 'Прогнозы',
  'forecast.viewLabel': 'Прогноз направления',
  'forecast.emptyState': 'Прогноза пока нет — запросите его у агента через инструмент `forecast`.',
  'forecast.conditionBannerLead': 'Прогноз — это',
  'forecast.conditionBannerStrong': 'калиброванная вероятность направления',
  'forecast.conditionBannerTail':
    '— отчёт об условии, а не совет. Каждый горизонт проходит или не проходит собственный внешний базовый порог.',
  'forecast.asOf': 'по состоянию на',
  'forecast.asOfSuffix': '(последний бар, который видели признаки)',
  'forecast.featureSet': 'набор признаков',
  'forecast.featuresPriceOnly': '— только ценовые признаки; внешние ряды не использовались',
  'forecast.exogenousSeries': '— внешние ряды:',
  'forecast.whySummary': 'Почему — на что опираются проверенные модели',
  'forecast.inputFreshness': 'Свежесть входных данных',
  'forecast.freshestPoint': '— свежайшая точка {ts}',
  'forecast.noObservablePoint': '— нет наблюдаемой точки',
  'forecast.artifactLead': 'полное объяснение сохранено в',
  'forecast.artifactTail': '(относительно каталога прогонов сайдкара)',
  'forecast.disclaimer':
    'Показатели мастерства — это внешняя точность направления из очищенной валидации со скользящим окном; базис — сильнейший из инерции и мажоритарного класса на тех же барах (ADR-0030). Маргинальное преимущество означает, что превышение было незначительным — рассматривайте его вероятности как слабое свидетельство.',
  'forecast.driversHeadingAhead': 'вперёд —',
  'forecast.topDrivers': 'главные факторы',
  'forecast.blockAriaLabel': 'Прогноз на {horizon} вперёд',
  'forecast.ahead': 'вперёд',
  'forecast.directionUp': 'Вверх',
  'forecast.directionDown': 'Вниз',
  'forecast.directionFlat': 'Флэт',
  'forecast.noEdgeStrong': 'Нет преимущества над базисом.',
  'forecast.noEdgeBody':
    'Модель не превзошла наивный базис вне выборки на этом горизонте, поэтому вероятность не показана — честное «не знаю» вместо выдуманного числа.',
  'forecast.outOfSample': 'вне выборки',
  'forecast.skill': 'мастерство',
  'forecast.unscored': 'без оценки',
  'forecast.vs': 'против',
  'forecast.baseline': 'базис',
  'forecast.margin': 'запас',
  'forecast.scoredBars': 'оценённых баров',
  'forecast.across': 'по',
  'forecast.folds': 'фолдов',
  'forecast.provenanceTitle': 'модель {model} · библиотеки {libs} · seed {seed}',
  'forecast.provenanceModelPrefix': 'модель',
  'forecast.trainedThrough': '· обучена до',
  'forecast.noModelTrained':
    'на этом горизонте модель не обучалась (недостаточно пригодной истории)',
  // Volatility forecast section (Plan 0077 phase 6).
  'forecast.volatilityLabel': 'Прогноз волатильности',
  'forecast.volatilityTitle': 'Волатильность',
  'forecast.volatilityLede':
    'Прогноз реализованной волатильности на горизонте — величина, а не направление.',
  'forecast.predictedVol': 'Прогноз волатильности',
  'forecast.volBand': 'Интервал 1σ',
  'forecast.baselineVol': 'Базовая линия',
  'forecast.perBarVol': 'RMS логарифмических доходностей за бар',
  'forecast.volNoEdgeStrong': 'Нет преимущества над базовой линией.',
  'forecast.volNoEdgeBody':
    'Модель не превзошла детерминированную базовую линию EWMA/персистентности вне выборки, поэтому честной оценкой волательности является базовое значение ниже — число EWMA/персистентности, а не выдуманный прогноз модели.',
  'forecast.qlike': 'QLIKE',
  'forecast.volDisclaimer':
    'Волатильность оценивается по QLIKE на дисперсии (меньше — лучше); базовая линия — сильнейшая из RiskMetrics EWMA и наивной персистентности на тех же барах (ADR-0070). Детерминированная базовая линия полезна даже когда модель ничего не добавляет.',
  // Regime forecast section (Plan 0077 phase 6).
  'forecast.regimeLabel': 'Прогноз режима',
  'forecast.regimeTitle': 'Режим',
  'forecast.regimeLede':
    'Скользящее состояние рынка (тренд × волатильность) и прогноз перехода на следующий период — условие, а не направление.',
  'forecast.currentRegime': 'Текущий режим',
  'forecast.regimeTransitionHeading': 'Режим следующего периода ({horizon})',
  'forecast.regimeCurrentTag': '(текущий)',
  'forecast.brier': 'Brier',
  'forecast.persistence': 'персистентность',
  'forecast.regimeNoEdgeStrong': 'Нет преимущества над персистентностью.',
  'forecast.regimeNoEdgeBody':
    'Модель перехода не превзошла наивную базовую линию персистентности (режим без изменений) вне выборки, поэтому честное ожидание на следующий период — что текущий режим сохранится.',
  'forecast.regimeDisclaimer':
    'Режим — это скользящая классификация по правилам (тренд × волатильность); модель перехода оценивается по многоклассовому Brier против базовой линии персистентности (ADR-0070). Условие, но не направление.',

  // ── Recommendations view (RecommendationsView.tsx) chrome ──
  'recommendations.advisoryRecommendationLabel': 'Консультативная рекомендация',
  'recommendations.empty':
    'Рекомендации пока нет — запросите её у агента через инструмент `recommend`.',
  'recommendations.advisoryOnly': 'Только рекомендательно.',
  'recommendations.advisoryBannerBody':
    'Это рекомендация, а не торговый ордер — ничто в этом приложении не может её исполнить. Агент рекомендует; решаете вы.',
  'recommendations.asOf': 'по состоянию на',
  'recommendations.lastClosedBar': '(последний закрытый бар, который видел базис)',
  'recommendations.direction': 'Направление',
  'recommendations.directionLong': 'лонг',
  'recommendations.directionShort': 'шорт',
  'recommendations.directionFlat': 'флэт — нет реализуемого преимущества',
  'recommendations.conviction': 'Уверенность',
  'recommendations.convictionDerived':
    'выведена (вероятность прогноза × протестированное преимущество), никогда не выдумана',
  'recommendations.advisoryLevelsLabel': 'Рекомендательные уровни',
  'recommendations.advisoryLevelsTitle':
    'Рекомендательные уровни — для вашего суждения, а не ордер',
  'recommendations.entryZone': 'Зона входа',
  'recommendations.advisoryTag': '(рекомендательно)',
  'recommendations.stop': 'Стоп',
  'recommendations.targetHeading':
    '{count, plural, one {Цель} few {Цели} many {Целей} other {Цели}}',
  'recommendations.rationaleLabel': 'Обоснование',
  'recommendations.why': 'Почему',
  'recommendations.basisLabel': 'Основа',
  'recommendations.whatBackedThisCall': 'Что подкрепило это решение',
  'recommendations.conditions': 'Условия',
  'recommendations.liveSignals': 'Живые сигналы',
  'recommendations.backtestedEdge': 'Протестированное преимущество',
  'recommendations.disclaimer':
    'Помечено как рекомендательное (ADR-0029): основа выше сопровождает каждое решение, а вердикт «флэт» — честное «нет реализуемого преимущества», а не выдуманное решение.',
  'recommendations.fusionChecksLabel': 'Проверки слияния',
  'recommendations.everyGateChecked': 'Каждый вентиль проверен',
  'recommendations.leg': 'нога',
  'recommendations.check': 'проверка',
  'recommendations.threshold': 'порог',
  'recommendations.actual': 'факт',
  'recommendations.result': 'результат',
  'recommendations.pass': 'пройдено',
  'recommendations.fail': 'ПРОВАЛ',
  'recommendations.checksNote':
    'Трасса фиксирует решение слияния; направленное решение означает, что все блокирующие вентили пройдены.',
  'recommendations.nonGatingTag': 'не блокирует',
  'recommendations.none': 'нет',
  'recommendations.notPartOfBasis': 'не входит в эту основу',
  'recommendations.forecast': 'Прогноз',
  'recommendations.forecastRanOnTier': 'Прогноз выполнен на наборе признаков {name}.',
  'recommendations.forecastRanOnFeatureSet': 'Прогноз выполнен на наборе признаков {x}.',
  // Direction-leg demotion + non-voting vol/regime inputs (Plan 0077 phase 5/6).
  'recommendations.directionLegLabel': 'Нога прогноза направления',
  'recommendations.directionLegGating': 'голосует',
  'recommendations.directionLegNonGating': 'присутствует, но не блокирует',
  'recommendations.directionLegGatingNote':
    'Прогноз направления превысил порог мастерства, поэтому он голосовал по этому решению.',
  'recommendations.directionLegNonGatingNote':
    'Прогноз направления не имел надёжного преимущества (запас мастерства вне выборки ниже порога), поэтому он не голосовал и не мог наложить вето на это решение — он лишь совещательный (ADR-0071). Решение опирается на условия, живой сигнал и протестированное преимущество.',
  'recommendations.directionLegMargin': 'запас мастерства вне выборки {margin}',
  'recommendations.directionLegNoMargin': 'нет оценённого запаса мастерства',
  'recommendations.nonVotingLabel': 'Не голосующие входы',
  'recommendations.nonVotingTitle': 'Размер и контекст (не голосуют)',
  'recommendations.nonVotingNote':
    'Волатильность и режим формируют размер, дистанцию стопа и убеждённость — они никогда не голосуют за направление и не переворачивают его (ADR-0071).',
  'recommendations.sizingTitle': 'Размер по волатильности',
  'recommendations.sizeFactor': 'Коэффициент размера',
  'recommendations.sizeFactorNote':
    'обратная волатильность · 1.00 = эталон · совещательно, не размер ордера',
  'recommendations.volUsed': 'Использованная волатильность',
  'recommendations.volSource': 'Источник',
  'recommendations.stopVolDistance': 'Дистанция стопа по волатильности',
  'recommendations.sizingNeutral': 'Нет пригодного значения волатильности — нейтральный размер.',
  'recommendations.regimeContextTitle': 'Контекст режима',
  'recommendations.currentRegime': 'Текущий режим',
  'recommendations.regimeTrusted':
    'модель перехода надёжна (превосходит персистентность вне выборки)',
  'recommendations.regimeUntrusted':
    'модель перехода ненадёжна — по умолчанию персистентность, убеждённость без изменений',
  'recommendations.convictionFactor': 'Коэффициент убеждённости',
  'recommendations.convictionFactorNote': 'множитель стабильности режима · 1.00 = нейтрально',
  'recommendations.regimeUndefined': 'не определён',

  // ── Technical Read view (TechnicalReadView.tsx) — Plan 0074, ADR-0068 ──
  'technicalRead.viewLabel': 'Технический разбор',
  'technicalRead.empty':
    'Технического разбора пока нет — запросите его у агента через инструмент `technical_read`.',
  'technicalRead.notCorroboratedTitle': 'Один индикатор — без подтверждения.',
  'technicalRead.notCorroboratedBody':
    'Это механический разбор одного индикатора, а не сводный вызов `recommend`. За ним нет прогноза, нет проверенного на истории преимущества и нет уверенности — он может расходиться с подтверждённой рекомендацией. Вы читаете его и определяете размер позиции сами.',
  'technicalRead.asOf': 'по состоянию на',
  'technicalRead.lastClosedBar': '(последний закрытый бар, который видел разбор)',
  'technicalRead.indicator': 'Индикатор',
  'technicalRead.direction': 'Направление',
  'technicalRead.directionLong': 'лонг',
  'technicalRead.directionShort': 'шорт',
  'technicalRead.directionFlat': 'нейтрально — нет чёткого направления',
  'technicalRead.regimeState': 'Состояние режима',
  'technicalRead.why': 'Механическое правило',
  'technicalRead.indicatorSupertrend': 'Supertrend',
  'technicalRead.indicatorEmaStack': 'Стек EMA',
  'technicalRead.indicatorMacd': 'MACD',
  'technicalRead.indicatorIchimoku': 'Ишимоку',
  'technicalRead.disclaimer':
    'Меньший консультативный уровень (ADR-0068): один названный индикатор, прочитанный по учебному правилу, без уверенности и без уровней входа/стопа/цели — так задумано. Для подтверждённого вызова с уровнями используйте сводный инструмент `recommend`.',

  // ── Convergence view (ConvergenceView.tsx) — Plan 0078, ADR-0041/0029 ──
  'convergence.viewLabel': 'Возможности сходимости',
  'convergence.empty':
    'Пока нет результатов скрининга — попросите агента запустить инструмент `find_convergence_opportunities`.',
  'convergence.disclaimerTitle': 'Факты, а не совет.',
  'convergence.disclaimerBody':
    'Это рынки, близкие к разрешению, с приложенными рисками — подразумеваемая доходность почти определённого исхода показана до вычета «хвоста» разрешения, а не как ожидаемая доходность. Оцените риск разрешения, ликвидность и блокировку капитала, прежде чем принимать решение самостоятельно.',
  'convergence.forQuery': 'По запросу',
  'convergence.opportunities': 'возможностей',
  'convergence.asOf': 'по состоянию на',
  'convergence.outcome': 'Почти определённый исход',
  'convergence.returnIfRight': 'Доходность при верном исходе',
  'convergence.returnGrossNote': 'до вычета «хвоста» разрешения — не ожидаемая доходность',
  'convergence.timeToResolution': 'Время до разрешения',
  'convergence.closesAt': 'закрытие',
  'convergence.volume': 'Объём',
  'convergence.resolutionRisk': 'Риск разрешения',
  'convergence.riskHeuristicNote': 'маркированная эвристика, не гарантия',
  'convergence.riskLevelLow': 'низкий',
  'convergence.riskLevelMedium': 'средний',
  'convergence.riskLevelHigh': 'высокий',
  'convergence.viewOnPolymarket': 'Открыть на Polymarket ↗',

  // ── Track-record view (TrackRecordView.tsx) — Plan 0080, ADR-0075 ──
  'trackRecord.title': 'История точности',
  'trackRecord.lede':
    'Как ранее сделанные рекомендации советника оправдались по реальной цене — с учётом стопа и целей каждой из них.',
  'trackRecord.disclaimer':
    'Фактическая история точности, а не совет — и никогда не повод действовать.',
  'trackRecord.loading': 'Загрузка истории точности…',
  'trackRecord.loadError': 'Не удалось загрузить историю точности.',
  'trackRecord.empty': 'Оценённых рекомендаций пока нет.',
  'trackRecord.sampleSize':
    '{n, plural, one {# оценённая рекомендация} few {# оценённые рекомендации} many {# оценённых рекомендаций} other {# оценённых рекомендаций}}',
  'trackRecord.insufficient':
    'Недостаточно рекомендаций для вывода — {n} из {min} необходимых. Проценты скрыты, пока выборка не станет достаточно большой, чтобы что-то значить.',
  'trackRecord.baselineDeltaTitle': 'Преимущество над базисом',
  'trackRecord.baselineDeltaLabel':
    'доля успехов против базиса «купи и держи» на горизонте (главное число)',
  'trackRecord.hitRate': 'Доля успехов',
  'trackRecord.baselineHitRate': 'Доля успехов базиса',
  'trackRecord.meanR': 'Средний R',
  'trackRecord.calibrationTitle': 'Калибровка',
  'trackRecord.brier': 'Оценка Бриера',
  'trackRecord.meanPredicted': 'Заявленная вероятность',
  'trackRecord.observedFreq': 'Реальная частота',
  'trackRecord.reliabilityBand': 'Заявленный диапазон',
  'trackRecord.colCount': 'n',
  'trackRecord.recentTitle': 'Недавно оценённые рекомендации',
  'trackRecord.recentEmpty': 'Нет оценённых рекомендаций для показа.',
  'trackRecord.colSymbol': 'Тикер',
  'trackRecord.colDirection': 'Направление',
  'trackRecord.colOutcome': 'Исход',
  'trackRecord.colRealizedR': 'Реализованный R',
  'trackRecord.colAsOf': 'На дату',
  'trackRecord.directionLong': 'Лонг',
  'trackRecord.directionShort': 'Шорт',
  'trackRecord.outcomeTargetHit': 'Цель достигнута',
  'trackRecord.outcomeStopped': 'Стоп',
  'trackRecord.outcomeTimeout': 'Тайм-аут',

  // ── Sidecar reason-codes (advisor fusion.py / forecast explain.py) ──
  // Directional rationale.
  'reason.forecast':
    'прогноз: P({direction})={prob} на {horizon_bars} бар(ов), {edge_strength}{_skill, plural, =1 { (мастерство вне выборки {skill} против базиса {baseline})} other {}}',
  'reason.signals_agree': 'живые сигналы согласны ({direction}): {strategies}',
  'reason.backtested_edge':
    'протестированное преимущество: walk-forward sharpe_mean {sharpe_mean} по {n_splits} фолдам ({strategy_id})',
  'reason.conditions': 'условия: тренд={trend}, моментум={momentum}, объём={volume}',
  // Flat verdict.
  'reason.no_actionable_edge': 'нет реализуемого преимущества',
  'blocker.forecast_no_edge':
    'прогноз не показывает преимущества над базисом (вероятность не отправлена)',
  'blocker.forecast_flat': 'направление прогноза — флэт или не определено',
  'blocker.signals_conflict': 'живые сигналы конфликтуют: лонг={long}, шорт={short}',
  'blocker.no_directional_signal': 'ни один живой сигнал стратегии не задаёт направление',
  'blocker.signals_disagree_forecast':
    'живые сигналы ({signal_dir}) расходятся с направлением прогноза ({forecast_dir})',
  'blocker.no_walk_forward': 'основа walk-forward бэктеста не предоставлена',
  'blocker.no_backtested_edge':
    'нет протестированного преимущества{_sharpe, plural, =1 {: walk-forward sharpe_mean {sharpe_mean}} other {}}',
  'blocker.edge_nonvoting_strategy':
    'преимущество walk-forward относится к {strategy_id}, которой нет среди согласных сигналов',
  // Fusion gate-check labels.
  'gate.alignment_scope': 'входы имеют общий символ/таймфрейм',
  'gate.alignment_asof': 'входы имеют общий опорный бар',
  'gate.conditions_read': 'снимок условий прочитан',
  'gate.forecast_probs_shipped': 'вероятности отправлены (базис превзойдён вне выборки)',
  'gate.forecast_argmax_directional': 'направление argmax является направленным',
  'gate.forecast_calibrated_prob': 'калиброванная P(направление)',
  'gate.signal_live_vote': 'живой голос: {strategy_id}',
  'gate.signal_no_conflict': 'нет конфликтующих живых голосов',
  'gate.signal_directional_vote': 'хотя бы один направленный живой голос',
  'gate.signal_agrees_forecast': 'живое направление согласуется с направлением прогноза',
  'gate.backtest_basis_supplied': 'основа walk-forward предоставлена',
  'gate.backtest_edge_positive': 'протестированное преимущество положительно (sharpe_mean > 0)',
  'gate.backtest_strategy_agrees': 'стратегия walk-forward среди согласных голосов',
  // Non-voting inputs + direction-leg demotion (Plan 0077 phase 5, ADR-0071).
  'gate.volatility_nonvoting': 'прогноз волатильности (не голосует: размер + стоп)',
  'gate.regime_nonvoting': 'прогноз режима (не голосует: убеждённость)',
  'reason.direction_leg_nongating':
    'нога прогноза направления присутствует, но не блокирует (запас мастерства вне выборки ниже {threshold}) — лишь совещательно, решение опирается на живой сигнал и протестированное преимущество',
  'reason.sizing':
    'волатильность (не голосует): значение {vol_source} → коэффициент размера {size_factor}',
  'reason.regime_context':
    'режим (не голосует): {current_regime}, {trusted, plural, =1 {надёжно} other {ненадёжно}} → коэффициент убеждённости {conviction_factor}',
  // Condition / signal facts.
  'condition.trend': 'тренд: {value}',
  'condition.momentum': 'моментум: {value}',
  'condition.volume': 'объём: {value}',
  'condition.candlestick': 'свеча: {pattern} ({direction})',
  'signal.vote': '{strategy_id}: позиция={position}{fresh, plural, =1 {, свежий сигнал} other {}}',
  // Forecast explanation constants.
  'disclaimer.importance':
    'Важность факторов — это внешняя перестановочная важность: связь внутри проверенной модели, а не причинность; коррелирующие входы делят вклад.',
  'note.no_scored_folds':
    'нет оценённых фолдов вне выборки на этом горизонте — важности не измерялись',

  // ── Enum labels (closed condition/signal vocabularies + passthrough enums) ──
  // Trend.
  'enum.trend.up': 'восходящий',
  'enum.trend.down': 'нисходящий',
  'enum.trend.sideways': 'боковой',
  // Momentum.
  'enum.momentum.overbought': 'перекуплен',
  'enum.momentum.bullish': 'бычий',
  'enum.momentum.neutral': 'нейтральный',
  'enum.momentum.bearish': 'медвежий',
  'enum.momentum.oversold': 'перепродан',
  // Volume.
  'enum.volume.heavy': 'высокий',
  'enum.volume.normal': 'обычный',
  'enum.volume.light': 'низкий',
  // Pattern direction.
  'enum.direction.bullish': 'бычий',
  'enum.direction.bearish': 'медвежий',
  'enum.direction.neutral': 'нейтральный',
  // Live-signal / recommendation direction.
  'enum.position.long': 'лонг',
  'enum.position.short': 'шорт',
  'enum.position.flat': 'флэт',
  // Candlestick pattern names.
  'enum.pattern.doji': 'дожи',
  'enum.pattern.hammer': 'молот',
  'enum.pattern.hanging_man': 'повешенный',
  'enum.pattern.marubozu': 'марубозу',
  'enum.pattern.bullish_engulfing': 'бычье поглощение',
  'enum.pattern.bearish_engulfing': 'медвежье поглощение',
  'enum.pattern.dark_cloud_cover': 'завеса из тёмных облаков',
  'enum.pattern.piercing_line': 'просвет в облаках',
  'enum.pattern.bullish_harami': 'бычья харами',
  'enum.pattern.bearish_harami': 'медвежья харами',
  'enum.pattern.morning_star': 'утренняя звезда',
  'enum.pattern.evening_star': 'вечерняя звезда',
  'enum.pattern.three_white_soldiers': 'три белых солдата',
  'enum.pattern.three_black_crows': 'три чёрные вороны',
  // Forecast edge strength.
  'enum.edge_strength.no_edge': 'нет преимущества над базисом',
  'enum.edge_strength.marginal': 'маргинальное преимущество',
  'enum.edge_strength.clear': 'явное преимущество',
  // Regime taxonomy (RegimeState — trend × volatility, Plan 0077 phase 2).
  'enum.regime.down_quiet': 'нисходящий, спокойно',
  'enum.regime.down_volatile': 'нисходящий, волатильно',
  'enum.regime.sideways_quiet': 'боковой, спокойно',
  'enum.regime.sideways_volatile': 'боковой, волатильно',
  'enum.regime.up_quiet': 'восходящий, спокойно',
  'enum.regime.up_volatile': 'восходящий, волатильно',
  // Volatility baseline kind (BaselineKind) + advisory sizing source (Plan 0077).
  'enum.vol_baseline.ewma': 'EWMA',
  'enum.vol_baseline.persistence': 'персистентность',
  'enum.vol_source.model': 'модель',
  'enum.vol_source.baseline': 'базовая линия',
  'enum.vol_source.none': 'нет пригодного',
  // Passthrough enums authored as labels on our side.
  'enum.crypto_regime.btc_led': 'ведёт BTC',
  'enum.crypto_regime.alt_structure': 'структура альтов',
  'enum.crypto_regime.risk_off_structure': 'структура ухода от риска',
  'enum.crypto_regime.neutral': 'нейтральный',
  'enum.fear_greed.extreme_fear': 'Крайний страх',
  'enum.fear_greed.fear': 'Страх',
  'enum.fear_greed.neutral': 'Нейтрально',
  'enum.fear_greed.greed': 'Жадность',
  'enum.fear_greed.extreme_greed': 'Крайняя жадность',

  // ── Fixed sidecar error details (client.ts localizeErrorDetail) ──
  'error.detail.agentModeOff': 'режим агента выключен',
  'error.detail.noWalletSource': 'источник позиций кошелька не настроен',
  'error.detail.noHistoricalPriceSource': 'источник исторических цен не настроен',
  'error.detail.noSecretsStore': 'хранилище секретов не настроено',
  'error.detail.noMcpSecretPath': 'путь к секрету MCP не настроен',
  'error.detail.noAlertingPersistence': 'хранилище оповещений не настроено',

  // ── Settings view (SettingsView.tsx) ──
  'settings.appearance.heading': 'Внешний вид',
  'settings.appearance.lede.pre': 'Выберите, как выглядит приложение. ',
  'settings.appearance.lede.system': 'Система',
  'settings.appearance.lede.post':
    ' следует настройке светлой/тёмной темы вашей операционной системы; «Светлая» и «Тёмная» фиксируют её независимо от ОС.',
  'settings.appearance.theme.label': 'Тема',
  'settings.appearance.theme.light': 'Светлая',
  'settings.appearance.theme.dark': 'Тёмная',
  'settings.appearance.theme.system': 'Система',
  'settings.appearance.language.label': 'Язык',
  'settings.chartStyle.heading': 'Стиль графика',
  'settings.chartStyle.lede.pre':
    'Измените цвета и толщину линий и маркеров свечного графика. Цвета и толщина сохраняются ',
  'settings.chartStyle.lede.perTheme': 'для каждой темы',
  'settings.chartStyle.lede.post': '; вы редактируете тему, которую график показывает сейчас.',
  'settings.mcp.heading': 'Доступ MCP',
  'settings.mcp.lede':
    'Claude Desktop и другие MCP-клиенты подключаются к сайдкару по адресу ниже с помощью bearer-токена. Токен долгоживущий и сохраняется между перезапусками приложения.',
  'settings.mcp.endpointUrl.label': 'URL конечной точки',
  'settings.mcp.bearerToken.label': 'Bearer-токен',
  'settings.mcp.loading': 'Загрузка…',
  'settings.mcp.hide': 'Скрыть',
  'settings.mcp.reveal': 'Показать',
  'settings.mcp.copied': 'Скопировано!',
  'settings.mcp.copy': 'Копировать',
  'settings.mcp.rotating': 'Ротация…',
  'settings.mcp.rotate': 'Сменить',
  'settings.mcp.rotateWarning':
    'Ротация создаёт новый токен и немедленно аннулирует существующий. Все активные MCP-клиенты придётся перенастроить на новый токен.',
  'settings.mcp.lifecycle.label': 'Жизненный цикл сайдкара',
  'settings.mcp.lifecycle.lede':
    'Сайдкар работает как отдельный процесс — закрытие этого окна его не останавливает. MCP-клиенты могут продолжать с ним общаться. Нажмите ниже, чтобы остановить его явно.',
  'settings.mcp.stopping': 'Остановка…',
  'settings.mcp.stopRequested': 'Запрошена остановка',
  'settings.mcp.stopSidecar': 'Остановить сайдкар',
  'settings.mcp.shutdownRequested':
    'Запрошено выключение сайдкара. Просмотрщик потеряет соединение с сайдкаром.',
  'settings.mcp.snippet.label': 'Сниппет для Claude Desktop',
  'settings.mcp.snippet.pre': 'Вставьте это в ',
  'settings.mcp.configFilename': 'claude_desktop_config.json',
  'settings.mcp.snippet.post':
    '. Сначала покажите токен, чтобы сниппет содержал реальное значение.',

  // ── Theme toggle (ThemeToggle.tsx) ──
  'themeToggle.system': 'Система',
  'themeToggle.light': 'Светлая',
  'themeToggle.dark': 'Тёмная',
  'themeToggle.ariaLabel': 'Тема: {current}. Активируйте, чтобы переключить на {next}.',
  'themeToggle.title': 'Тема: {current}',

  // ── Chart-style controls (ChartStyleControls.tsx) ──
  'chartStyle.candleTypeLabel': 'Тип свечей',
  'chartStyle.lineAreaNotePre': 'Линия и Область рисуются одним цветом (цвет ',
  'chartStyle.candleUp': 'Свеча вверх',
  'chartStyle.lineAreaNotePost': '). Переключитесь на Свечи или OHLC-бары, чтобы изменить его.',
  'chartStyle.editingPre': 'Редактируется ',
  'chartStyle.editingPost':
    ' тема — смените тему во «Внешнем виде», чтобы редактировать другой набор.',
  'chartStyle.widthLabel': 'Толщина',
  'chartStyle.colorLabel': 'Цвет',
  'chartStyle.resetButton': 'Сбросить стиль графика',

  // ── Chart legend (ChartLegend.tsx) — Plan 0096 phase 2/3 ──
  'chartLegend.ariaLabel': 'Легенда слоёв графика',
  'chartLegend.settingsAria': 'Стиль {layerName}',
  'chartLegend.presetLabel': 'Пресет',
  'chartLegend.presetCustom': 'Свой',
  'chartLegend.savePreset': 'Сохранить как…',
  'chartLegend.presetNamePlaceholder': 'Название пресета',
  'chartLegend.preset.clean': 'Чистый',
  'chartLegend.preset.trend': 'Тренд',
  'chartLegend.preset.meanReversion': 'Возврат к среднему',
  'chartLegend.preset.patterns': 'Паттерны',

  // ── Layers panel (LayersPanel.tsx) ──
  'layers.panelAriaLabel': 'Слои графика',
  'layers.heading': 'Слои',
  'layers.toggleAria': 'Переключить {layerName}',
  'layers.resizeAria': 'Изменить ширину панели слоёв',
  'layers.addIndicator': '+ Индикатор',
  'layers.kindLabel': 'Индикатор',
  'layers.periodLabel': 'Период',
  'layers.stdDevLabel': 'Ст. откл. (k)',
  'layers.addButton': 'Добавить',
  'layers.removeAria': 'Удалить {layerName}',
  'layers.invalidPeriod': 'Период должен быть целым числом больше 0.',
  'layers.invalidStdDev': 'Стандартное отклонение должно быть больше 0.',
  'layers.kind.ema': 'EMA',
  'layers.kind.sma': 'SMA',
  'layers.kind.bbands': 'Полосы Боллинджера',
  'layers.kind.supertrend': 'Supertrend',
  'layers.kind.ichimoku': 'Ишимоку',
  'layers.kind.stochastic': 'Стохастик',
  'layers.kind.stoch_rsi': 'Стохастический RSI',
  'layers.kind.cci': 'CCI',
  'layers.kind.williams_r': 'Williams %R',
  'layers.kind.roc': 'Скорость изменения',
  'layers.kind.mfi': 'Индекс денежного потока',
  'layers.kind.cmf': 'Денежный поток Чайкина',
  'layers.kind.ad_line': 'Линия A/D',
  'layers.kind.rsi': 'RSI',
  'layers.kind.macd': 'MACD',
  'layers.kind.fibonacci': 'Фибоначчи',
  'layers.kind.pivot_points': 'Опорные точки',
  'layers.kind.anchored_vwap': 'Привязанный VWAP',

  // ── Glossary tooltip chrome (GlossaryTerm.tsx) ──
  'glossary.howComputedLabel': 'Как вычисляется',
  'glossary.whatItMeansLabel': 'Что это значит',
} satisfies Record<keyof typeof en, string> satisfies Catalog
