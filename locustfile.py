import uuid

from locust import HttpUser, between, task


class PaymentApiUser(HttpUser):
    wait_time = between(0.2, 1.0)

    def on_start(self) -> None:
        self.api_key = "merchant-development-key"
        self.payment_ids: list[str] = []

    def create_payment(self) -> None:
        unique_value = uuid.uuid4().hex

        with self.client.post(
            "/v1/payments",
            headers={
                "X-API-Key": self.api_key,
                "Idempotency-Key": (
                    f"load-payment-{unique_value}"
                ),
                "X-Test-Scenario": "success",
            },
            json={
                "user_id": f"load-user-{unique_value}",
                "amount_cents": 10000,
                "currency": "INR",
            },
            name="POST /v1/payments",
            catch_response=True,
        ) as response:
            if response.status_code not in {
                200,
                201,
                202,
            }:
                response.failure(
                    f"Unexpected status "
                    f"{response.status_code}: "
                    f"{response.text}"
                )
                return

            try:
                payment = response.json()
                self.payment_ids.append(payment["id"])
                response.success()
            except (
                ValueError,
                KeyError,
            ) as exc:
                response.failure(
                    f"Invalid response: {exc}"
                )

    @task(3)
    def create_payment_task(self) -> None:
        self.create_payment()

    @task(6)
    def retrieve_payment(self) -> None:
        if not self.payment_ids:
            self.create_payment()
            return

        payment_id = self.payment_ids[
            -1
        ]

        with self.client.get(
            f"/v1/payments/{payment_id}",
            headers={
                "X-API-Key": self.api_key,
            },
            name="GET /v1/payments/{payment_id}",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(
                    f"Unexpected status "
                    f"{response.status_code}: "
                    f"{response.text}"
                )

    @task(1)
    def retrieve_payment_events(self) -> None:
        if not self.payment_ids:
            return

        payment_id = self.payment_ids[
            -1
        ]

        with self.client.get(
            (
                f"/v1/payments/"
                f"{payment_id}/events"
            ),
            headers={
                "X-API-Key": self.api_key,
            },
            name=(
                "GET "
                "/v1/payments/{payment_id}/events"
            ),
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                response.success()
            else:
                response.failure(
                    f"Unexpected status "
                    f"{response.status_code}: "
                    f"{response.text}"
                )