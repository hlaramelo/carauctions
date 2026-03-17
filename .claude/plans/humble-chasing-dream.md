# Car Auction Deal Finder - Plano de Automacao

## Context

Henrique importa carros luxury/exoticos ($80k+ USD) dos EUA para revenda no Brasil. Hoje o processo de encontrar bons deals eh manual — monitorando Copart, Bring a Trailer, PC Car Market e Hemmings. O objetivo eh automatizar a descoberta de oportunidades, calcular viabilidade financeira e receber alertas quando um deal interessante aparecer.

**Output desejado:** Planilha automatica + Telegram bot + Email alerts com deals rankeados.

---

## Arquitetura Geral

```
[Scrapers] --> [Database] --> [Deal Scoring Engine] --> [Alerts]
                                                    --> [Google Sheets]
```

Stack: **Python** (scraping, data, automacao)

---

## Modulo 1: Data Collection (Scrapers)

### 1.1 Fontes de Leilao (US)

| Plataforma | Metodo | Frequencia |
|---|---|---|
| **Copart** | API nao-oficial / Selenium scraping | A cada 30min |
| **Bring a Trailer** | RSS feed + scraping de listings | A cada 15min (leiloes com timer) |
| **PC Car Market** | Scraping de listings | A cada 1h |
| **Hemmings** | Scraping de listings | A cada 1h |

**Dados coletados por veiculo:**
- Make, Model, Year, Trim
- VIN
- Preco atual / bid atual / reserve (se disponivel)
- Condicao (mileage, damage report, title status)
- Localizacao (estado US — afeta custo de shipping)
- Fotos (URLs)
- Data/hora de encerramento do leilao
- Link direto pro listing

**Implementacao:**
- `scrapy` ou `playwright` para sites que precisam de JS rendering
- Rate limiting e rotacao de user-agents pra evitar bloqueio
- Proxy rotation se necessario (residential proxies)
- Cada scraper como modulo independente com interface comum

### 1.2 Fontes de Preco Brasil

| Fonte | Metodo | Dados |
|---|---|---|
| **Tabela FIPE** | API publica (fipe.org.br) | Preco referencia por marca/modelo/ano |
| **Webmotors** | Scraping de resultados | Precos reais de mercado (anuncios ativos) |
| **OLX/iCarros** | Scraping de resultados | Precos reais de mercado |

**Dados coletados:**
- Preco medio de venda no BR para aquele make/model/year
- Range de precos (min, median, max)
- Numero de anuncios ativos (liquidez do mercado)
- Tempo medio de anuncio (velocidade de venda)

---

## Modulo 2: Cost Calculator

### 2.1 Custos de Importacao (parametrizaveis)

```python
class ImportCostCalculator:
    # Shipping US -> BR
    shipping_cost: float          # ~$2,500-$5,000 dependendo do porto
    inland_freight_us: float      # Transporte ate o porto US

    # Impostos BR (sobre valor CIF)
    imposto_importacao: float     # 35% sobre CIF
    ipi: float                    # 25-55% dependendo da cilindrada
    pis_cofins: float             # ~11.6%
    icms: float                   # 12-18% dependendo do estado

    # Outros custos
    despachante: float            # ~R$3,000-5,000
    armazenagem: float            # Variavel
    homologacao: float            # Se aplicavel

    def total_landed_cost(self, auction_price, vehicle_specs) -> float:
        ...

    def estimated_profit(self, auction_price, br_market_price) -> float:
        ...
```

### 2.2 Regras de Negocio

- IPI varia por cilindrada (tabela parametrizavel)
- ICMS varia por estado de destino
- Cambio USD/BRL atualizado automaticamente (API do BCB)
- Margem minima configuravel (ex: so mostrar deals com >20% margem estimada)

---

## Modulo 3: Deal Scoring Engine

### 3.1 Score de Oportunidade (0-100)

Fatores ponderados (pesos configuraveis):

| Fator | Peso | Descricao |
|---|---|---|
| **Margem estimada %** | 40% | (Preco revenda BR - Custo total) / Custo total |
| **Liquidez no BR** | 20% | Qtd de anuncios ativos + velocidade de venda |
| **Condicao do veiculo** | 15% | Mileage, title status, damage |
| **Tempo restante** | 15% | Urgencia — leiloes acabando em breve |
| **Historico de precos** | 10% | Preco atual vs. media historica pra aquele modelo |

### 3.2 Filtros Configuraveis

```yaml
filters:
  min_year: 2018
  max_mileage: 50000
  title_status: ["clean", "rebuilt"]  # excluir salvage se quiser
  min_margin_pct: 15
  max_auction_price_usd: 200000
  makes: ["Porsche", "BMW", "Mercedes-Benz", "Ferrari", "Lamborghini"]
  # ou vazio pra todas
  exclude_states: ["HI", "AK"]  # estados com shipping caro
```

---

## Modulo 4: Database

**SQLite** para comecar (simples, sem infra), migrar pra **PostgreSQL** se escalar.

### Tabelas principais:
- `vehicles` — listings ativos com todos os dados coletados
- `price_history` — historico de bids/precos por veiculo
- `br_market_prices` — precos de referencia no BR por make/model/year
- `deals` — deals calculados com score e P&L estimado
- `alerts_sent` — log de alertas enviados (evitar duplicatas)
- `config` — filtros e parametros do usuario

---

## Modulo 5: Notifications & Output

### 5.1 Telegram Bot

- Bot dedicado que envia mensagens formatadas
- Cada alert inclui: foto, make/model/year, preco atual, margem estimada, score, link direto
- Comandos interativos:
  - `/deals` — top 10 deals do momento
  - `/watch VIN` — acompanhar veiculo especifico
  - `/filters` — ver/editar filtros ativos
  - `/pause` / `/resume` — pausar alertas

### 5.2 Email Alerts

- Resumo diario (digest matinal) com melhores deals
- Alert instantaneo pra deals com score > threshold configuravel
- HTML formatado com fotos e links

### 5.3 Google Sheets (output principal)

- Sheet atualizada automaticamente via `gspread`
- Abas:
  - **Active Deals** — todos os deals ativos rankeados por score
  - **Watchlist** — veiculos que voce esta acompanhando
  - **Historico** — deals passados (pra analise retroativa)
  - **Config** — parametros editaveis direto na planilha
- Conditional formatting automatico (verde = boa margem, vermelho = ruim)

---

## Modulo 6: Scheduling & Infrastructure

### 6.1 Execution

- **Scheduler:** `APScheduler` ou `cron` jobs
- Scrapers rodam em intervalos diferentes (ver tabela Modulo 1)
- Deal scoring roda apos cada batch de scraping
- Alerts disparam em real-time quando novo deal atende criterios

### 6.2 Infra (opcoes)

| Opcao | Custo | Pros | Contras |
|---|---|---|---|
| **Local (seu PC)** | $0 | Simples | Precisa estar ligado |
| **VPS (DigitalOcean/Hetzner)** | ~$5-10/mes | Sempre on, barato | Setup manual |
| **AWS Lambda + EventBridge** | ~$2-5/mes | Serverless, escala | Mais complexo |

**Recomendacao:** comecar com VPS barata (Hetzner $5/mes), migrar se necessario.

### 6.3 Monitoramento

- Logging com `loguru`
- Alerta no Telegram se algum scraper falhar
- Health check diario

---

## Estrutura do Projeto

```
carauctions/
├── config/
│   ├── settings.yaml          # Filtros, thresholds, credenciais
│   └── import_costs.yaml      # Tabela de impostos e custos
├── scrapers/
│   ├── base.py                # Classe base com interface comum
│   ├── copart.py
│   ├── bring_a_trailer.py
│   ├── pc_car_market.py
│   ├── hemmings.py
│   └── br_market/
│       ├── fipe.py
│       ├── webmotors.py
│       └── olx.py
├── models/
│   ├── vehicle.py             # Dataclasses / SQLAlchemy models
│   ├── deal.py
│   └── database.py
├── engine/
│   ├── cost_calculator.py     # Calculo de custo total de importacao
│   ├── deal_scorer.py         # Scoring algorithm
│   └── currency.py            # Cambio USD/BRL via BCB
├── notifications/
│   ├── telegram_bot.py
│   ├── email_sender.py
│   └── sheets_sync.py        # Google Sheets sync
├── scheduler.py               # Orquestracao dos jobs
├── main.py
└── requirements.txt
```

---

## Plano de Execucao (Fases)

### Fase 1: MVP (1-2 semanas)
1. Scraper do **Bring a Trailer** (mais facil, dados estruturados)
2. API da **FIPE** pra precos de referencia BR
3. **Cost Calculator** basico com impostos fixos
4. **Deal Scorer** simples (margem + liquidez)
5. Output pra **Google Sheets**
6. **Telegram bot** basico com alertas

### Fase 2: Expansao de Fontes (semana 3-4)
7. Scraper **Copart**
8. Scraper **PC Car Market** + **Hemmings**
9. Scraping **Webmotors/OLX** pra precos reais de mercado BR
10. Email alerts (digest diario)

### Fase 3: Refinamento (semana 5+)
11. Historico de precos e analise de tendencia
12. Deal Scorer mais sofisticado (ML se tiver dados suficientes)
13. Comandos interativos no Telegram (`/watch`, `/filters`)
14. Dashboard web simples (opcional, Streamlit)

---

## Verificacao / Como Testar

1. Rodar cada scraper individualmente e validar dados coletados
2. Testar cost calculator com deals conhecidos (comparar com calculo manual)
3. Validar scores comparando com deals que voce ja fez no passado
4. Confirmar que alertas chegam no Telegram e email
5. Verificar que Google Sheets atualiza corretamente
6. Teste end-to-end: inserir listing fake → verificar se aparece com score correto → alerta dispara
