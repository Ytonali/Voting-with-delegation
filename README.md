## Голосование с делегированием (EIP-712) и утилита отправки транзакций

### Установка

```bash
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell
pip install -r requirements.txt
```

### Модуль голосования: `voting_with_delegation.py`

Возможности:
- Оффчейн делегирование через EIP-712 (подпись делегатором, проверка на бэкенде)
- Подсчёт эффективной мощности голосов с учётом цепочек делегирования
- Предложения с дедлайном голосования, варианты: `yes`, `no`, `abstain`
- Защита: `nonce`, `deadline` (expiry), анти-циклическая делегация

Пример:
```python
from voting_with_delegation import VotingWithDelegation
from eth_account import Account
from eth_account.messages import encode_structured_data

v = VotingWithDelegation(chain_id=1, verifying_contract="0x000000000000000000000000000000000000dEaD")

# Инициализация весов
v.add_voter("0x1111111111111111111111111111111111111111", 3)
v.add_voter("0x2222222222222222222222222222222222222222", 5)

# Оффчейн делегирование (подписывает делегатор)
delegator = Account.create()
delegatee = Account.create()
deadline = 2**31

msg = v.build_delegation_message(
    delegator=delegator.address,
    delegatee=delegatee.address,
    deadline=deadline,
)
typed = v.verifier.build_typed_data(msg)
signed = Account.sign_message(encode_structured_data(primitive=typed), delegator.key)

# Применение делегирования
v.apply_delegation_signature(
    signature=signed.signature.hex(),
    delegator=delegator.address,
    delegatee=delegatee.address,
    nonce=msg.nonce,
    deadline=deadline,
)

# Создание предложения
import time
closes_at = int(time.time()) + 3600
v.create_proposal(
    proposal_id="prop-1",
    title="Increase limit",
    description="Up limit to 10",
    closes_at=closes_at,
)

# Голосует конечный держатель мощности (делегат)
weight, tallies = v.vote(
    proposal_id="prop-1",
    voter=delegatee.address,
    choice="yes",
)
print(weight, tallies)
```

### Асинхронная отправка транзакции: `eth_async_sender.py`

Запуск:
```bash
python eth_async_sender.py
```
Перед запуском укажите приватный ключ в переменной `priv_key` внутри файла.


