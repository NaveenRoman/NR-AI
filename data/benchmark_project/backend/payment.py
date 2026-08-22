class PaymentService:
    def process_payment(self, user_id: int, amount: float) -> dict:
        if amount <= 0:
            raise ValueError('Payment amount must be positive.')
        return {'status': 'success', 'transaction_id': f'txn_{user_id}_{int(amount)}'}
