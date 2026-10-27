# Референсы для разработки сложных банковских задач

Практический каталог заданий, проверок и идей для Banking.ru.
Собран из наших исследований и концепта; пути к выбранным заданиям и проверкам
сверены с публичными репозиториями 27 октября 2026 года. Модельные прогоны
и исполнение внешних оценщиков в рамках подготовки документа не выполнялись.

Цели и обязательные правила — в [концепте](../banking-ru.md), разработка
и объём проверок — в [development.md](development.md). Этот каталог
не меняет контракт DB × ACTION и не назначает новые требования нашим задачам.

## Как выбирать пример

1. Открой конкретное задание, доступные агенту источники и проверку результата.
2. Разбери сохранённую траекторию, если она есть: что агент получил и сделал,
   какое условие нарушил, почему проверка обоснованна.
3. Отдели содержательную ошибку от дефекта задания, оценщика или инфраструктуры.
4. Перенеси один предметный механизм, согласуй его с банковским процессом
   и доступными источниками. Проверь правильный путь, альтернативу и контрпример.
5. Только затем измеряй сложность на моделях в разрешённом объёме.

Публичный ноль — повод для исследования, а не доказательство качества задания.
Длина траектории и объём корпуса тоже не доказывают сложность. Частичный reward
не равен доле решённых задач. Текущие рейтинги моделей здесь не утверждаются.

Уровни свидетельств:

- **Наш исторический разбор:** есть прежний анализ или воспроизведение определённой версии.
- **Кандидат:** задание и проверка доступны, но перенос механизма и корректность
  оценивания требуют отдельного аудита.
- **Библиотека примеров:** источник для дальнейшего отбора без выбранного трудного кейса.

## Закреплённые версии

| Источник | Commit для ссылок ниже |
|---|---|
| τ-Knowledge / banking_knowledge | `fc0055dc4e0a316c3f83133267fbd6faaa770992` — версия прежних исследований |
| MCPMark | `cd45b7f57923b9b3985467f5139927575f83141c` — снимок для этого каталога |
| LHTB | `7ca0cacea9bf6a0694eaad7306bed794eaaaabce` — снимок для этого каталога |
| Terminal-Bench | `6964e9252c90227fc1cfa26cbc59aee4134792e4` — снимок для этого каталога |

Закрепление файла не подтверждает, что опубликованный результат модели относится
к тому же commit. Перед сравнением отдельно сопоставляй версии задания,
проверки, среды, модели и harness. Старые результаты не объявляются измерением
нового снимка только по совпадению имени задачи.

## τ-Knowledge: банковские процессы

У этих четырёх заданий в закреплённой версии `reward_basis=["DB"]`.
Эталон и критерии находятся в JSON задания. Сравнение состояния реализовано
в [evaluator_env.py](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator_env.py), сборка результата —
в [evaluator.py](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/src/tau2/evaluator/evaluator.py). Это устройство внешнего
бенчмарка, а не основание убрать ACTION у нас.

| Задание и критерии | Что можно перенять | Граница |
|---|---|---|
| [task_053](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/data/tau2/domains/banking_knowledge/tasks/task_053.json) | Две связанные цели: действие по первой меняет возможность выполнить вторую. В банке — выбрать порядок, сохраняющий все достижимые обязательства | В Wiki есть исторический разбор и воспроизведение одной неуспешной попытки. Не является новой проверкой частоты ошибок |
| [task_015](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/data/tau2/domains/banking_knowledge/tasks/task_015.json) | Обнаружение операции и выдача нужного клиентского действия вместо попытки сделать всё агентом | Прежний разбор отмечал недоступный второй фактор в выбранном агентом пути. Не переносить недоступность данных как сложность |
| [task_020](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/data/tau2/domains/banking_knowledge/tasks/task_020.json) | Полнота сверки: учитывать разные виды расхождений, а не только удобные недоплаты | Коррекция избыточного начисления должна иметь основание в поручении и политике; это не автоматическое разрешение списывать деньги |
| [task_026](https://github.com/sierra-research/tau2-bench/blob/fc0055dc4e0a316c3f83133267fbd6faaa770992/data/tau2/domains/banking_knowledge/tasks/task_026.json) | Точность расчёта, округление, полнота обработки операций | В прежнем разборе отмечено расхождение правила округления и gold. Сначала аудит; не использовать как чистое доказательство сложности |

Опубликованные траектории для отдельного изучения:
[Opus](https://sierra-tau-bench-public.s3.us-west-2.amazonaws.com/submissions/claude-opus-5_sierra_2026-08-04/trajectories/banking_knowledge_results.json),
[Sol](https://sierra-tau-bench-public.s3.us-west-2.amazonaws.com/submissions/gpt-5-6-sol_sierra_2026-08-04/trajectories/banking_knowledge_results.json).
Числа попыток и причины ошибок бери из записи конкретной версии; заново
эти результаты при подготовке каталога не пересчитывались.

## MCPMark: отбор данных и расчёт

| Задание | Проверка | Идея для Banking.ru |
|---|---|---|
| [budget_computation](https://github.com/eval-sys/mcpmark/blob/cd45b7f57923b9b3985467f5139927575f83141c/tasks/filesystem/standard/desktop_template/budget_computation/description.md) | [verify.py](https://github.com/eval-sys/mcpmark/blob/cd45b7f57923b9b3985467f5139927575f83141c/tasks/filesystem/standard/desktop_template/budget_computation/verify.py) | Найти релевантные расходы среди разных файлов, исключить неподходящие категории, связать позиции с источниками и пересчитать общий итог |

Verifier проверяет файл результата, записи, пути/количества и суммы.
Наш перенос — сверка покупок, возвратов и выплат с обоснованными включениями
и исключениями. Точное форматирование отчёта не переносится как самостоятельная
цель банковского поручения без предметной необходимости.

[Опубликованный результат выбранного кейса](https://github.com/eval-sys/mcpmark-experiments/blob/main/verified/task_results/desktop_template__budget_computation.json)
сохранён как указатель прежнего исследования; ссылка изменяемая. Причины
провалов и соответствие закреплённой выше версии требуют отдельного разбора.
Статус механизма для нашего банка — **кандидат**.

## LHTB: сопоставление источников и согласованность

| Задание | Где проверяется | Что можно перенять |
|---|---|---|
| [satellite-flood-change-detection-audit](https://github.com/zli12321/LHTB/blob/7ca0cacea9bf6a0694eaad7306bed794eaaaabce/tasks/satellite-flood-change-detection-audit/instruction.md) | [grade.py](https://github.com/zli12321/LHTB/blob/7ca0cacea9bf6a0694eaad7306bed794eaaaabce/tasks/satellite-flood-change-detection-audit/tests/grade.py), [test.sh](https://github.com/zli12321/LHTB/blob/7ca0cacea9bf6a0694eaad7306bed794eaaaabce/tasks/satellite-flood-change-detection-audit/tests/test.sh) | Сопоставление состояния до/после, исключение непригодных входов, проверка выводов по нескольким признакам |
| [gdal-proj-raster-regression](https://github.com/zli12321/LHTB/blob/7ca0cacea9bf6a0694eaad7306bed794eaaaabce/tasks/gdal-proj-raster-regression/instruction.md) | [test_outputs.py](https://github.com/zli12321/LHTB/blob/7ca0cacea9bf6a0694eaad7306bed794eaaaabce/tasks/gdal-proj-raster-regression/tests/test_outputs.py), [test.sh](https://github.com/zli12321/LHTB/blob/7ca0cacea9bf6a0694eaad7306bed794eaaaabce/tasks/gdal-proj-raster-regression/tests/test.sh) | Согласованность преобразований и расчётов при разных условиях; проверка результата независимым вычислением |
| [great-expectations-audit](https://github.com/zli12321/LHTB/blob/7ca0cacea9bf6a0694eaad7306bed794eaaaabce/tasks/great-expectations-audit/instruction.md) | [test_outputs.py](https://github.com/zli12321/LHTB/blob/7ca0cacea9bf6a0694eaad7306bed794eaaaabce/tasks/great-expectations-audit/tests/test_outputs.py), [test.sh](https://github.com/zli12321/LHTB/blob/7ca0cacea9bf6a0694eaad7306bed794eaaaabce/tasks/great-expectations-audit/tests/test.sh) | Сверка заказов, платежей и возвратов, связность данных, исключение некорректных записей и воспроизводимость расчёта |

Банковская адаптация — отличать реальные движения от заявок, связывать записи
между системами и подтверждать каждую часть расчёта. Терминальные требования
к библиотекам и формату файлов не становятся требованиями к банковскому агенту.
Наличие hidden-тестов у референса не разрешает скрывать от нашего агента правила.

[Каталог опубликованных запусков](https://huggingface.co/datasets/IntelligenceLab/LHTB-leaderboard/tree/main/submissions/longer-horizon-terminal-bench-2.0/candidate-77)
— источник дальнейшего исследования, а не подтверждение трудности наших задач.
Все три банковских переноса здесь имеют статус **кандидатов**.

## Terminal-Bench: ограничения и последствия действий

| Задание | Где проверяется | Что можно перенять |
|---|---|---|
| [cargo-flight-dispatch](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/cargo-flight-dispatch/instruction.md) | [test_outputs.py](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/cargo-flight-dispatch/tests/test_outputs.py), [test.sh](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/cargo-flight-dispatch/tests/test.sh) | Одновременное соблюдение нескольких ограничений; допустимость плана целиком, а не только отдельных шагов |
| [freight-dispatch-shift](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/freight-dispatch-shift/instruction.md) | [checks.py](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/freight-dispatch-shift/tests/checks.py), [test.sh](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/freight-dispatch-shift/tests/test.sh) | События меняют условия; ранее построенный план нужно актуализировать перед исполнением |
| [data-anonymization](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/data-anonymization/instruction.md) | [test_outputs.py](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/data-anonymization/tests/test_outputs.py), [test.sh](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/data-anonymization/tests/test.sh) | Конфиденциальность при сохранении необходимых связей и полезности результата |
| [ontology-kg-querying](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/ontology-kg-querying/instruction.md) | [test_outputs.py](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/ontology-kg-querying/tests/test_outputs.py), [test.sh](https://github.com/harbor-framework/terminal-bench/blob/6964e9252c90227fc1cfa26cbc59aee4134792e4/tasks/ontology-kg-querying/tests/test.sh) | Сопоставление разнесённых фактов и редакций, сохранение исходных данных, эквивалентность числовых результатов |

Для банка это гипотезы: изменение суммы/доступности после события, совместимость
частей урегулирования, защита данных и сопоставление договоров с операциями.
Мы проверяем решение банковского поручения; написание CLI и инженерные требования
этих кейсов напрямую не переносим.

В прежнем исследовании были указатели на претензии к условиям и проверкам
первых двух заданий. Это повод сверить инструкции с verifier конкретной версии,
а не доказательство дефекта нынешних файлов. Старый
[сторонний разбор](https://github.com/OpenAgentsInc/openagents/blob/main/docs/terminal-bench/2026-09-23-tb4-failure-analysis.md)
и [экспорт результатов](https://github.com/OpenAgentsInc/openagents/blob/main/bench/terminal-bench/reference/tb4-leaderboard.json)
не заменяют первичные файлы выше. Нового аудита корректности здесь нет.

## Приоритет идей для нашей рабочей группы

Следующие пункты — направления разработки, а не готовые требования к задачам:

| Приоритет | Механизм | Пример адаптации |
|---|---|---|
| 1 | Изменение применимости сведений после события | Обновление сверки меняет доступную выплату; агент должен получить актуальные условия до исполнения |
| 2 | Связанные поручения | Одна операция закрывает возможность другой; нужно сохранить обе цели клиента |
| 3 | Полнота и расчёты | Учесть покупки, частичные возвраты, уже проведённые выплаты и остаток без двойного возмещения |
| 4 | Совместные исключения | Продукт, редакция, вид операции и состояние совместно определяют допустимый результат |
| 5 | Разделённые системы | CRM зарегистрировала обращение, но банк ещё не исполнил операцию или не доставил документ |

Агент исследует документы самостоятельно. По нашему текущему концепту получение
необходимых актуальных источников проверяется до зависимых действий; способ
поиска и число запросов не фиксируются. Исторические предложения убрать любые
проверки чтения не являются действующей инструкцией.

В прежних прототипах Bank 2 рост длины поиска не доказал устойчивую сложность;
успех или провал старого снимка не переносится на текущий Bank 2. Не копируй
старые суммы, ID, ограничения ошибок и формулы оценки без проверки версии.

## Другие источники для дальнейшего отбора

| Источник | Для чего смотреть | Текущая граница |
|---|---|---|
| [WildClawBench](https://github.com/InternLM/WildClawBench), [траектории](https://huggingface.co/datasets/internlm/WildClawBench-Trajectories) | Рабочие среды и траектории для выбора новых кейсов | Конкретный трудный кейс здесь не выбран |
| [PAC-1 handbook](https://github.com/bitgn/challenges/blob/main/pac/handbook.md), [публичные логи](https://github.com/l-eugine/pac1-sgr-agent) | Процессы с инструментами и состоянием | Нет подтверждённого здесь провала выбранной сильной модели |
| [Toolathlon](https://github.com/hkust-nlp/Toolathlon), [траектории](https://huggingface.co/datasets/hkust-nlp/Toolathlon-Verified_Trajectories) | Составные задачи по нескольким инструментам | Ранее отмечалась необходимость одобрения доступа к траекториям; условия проверять отдельно |
| [DAYJOB](https://github.com/surge-ai/dayjob) | Финансовые рабочие процессы | Конкретная трудная задача и её проверка в этом каталоге не отобраны |

Это библиотеки примеров; перечисление не подтверждает текущую позицию
бенчмарка в рейтинге или валидность всех его задач.

## Референсы организации разработки

- [OpenAI Harness Engineering](https://openai.com/ru-RU/index/harness-engineering/):
  короткая карта проекта, подробные документы по темам, проверяемые ограничения
  и обратная связь. Применяем к среде разработчика, отдельно от проверяемого агента.
- [Very AI dev-flow](https://github.com/redmadrobot-rnd/very-ai-framework/blob/main/DEV-FLOW.md):
  явный путь от постановки до проверяемого результата. Конкретный Git-процесс
  и разрешённые действия определяет наш [development.md](development.md).

## Происхождение исторических выводов и поддержка каталога

Исходное исследование хранится в Wiki Evaluation Platform:
`references/Сложные задачи агентных бенчмарков и развитие Bank2.md`, версия
25 октября 2026 года. Оно содержит исторические эксперименты и ссылки на
доказательства. Wiki и игнорируемые локальные артефакты не являются обязательной
зависимостью чтения этого каталога или запуска бенчмарка.

При добавлении примера укажи задание, проверку, commit, предметный механизм,
предлагаемый банковский перенос и уровень доказательства. Успех ссылок — не
прохождение эталона. Для нового вывода о трудности нужны реальные попытки
и разбор оценок. При изменении источника сохраняй версию прежнего вывода;
не заменяй историческое доказательство ссылкой на другой commit без сверки.
