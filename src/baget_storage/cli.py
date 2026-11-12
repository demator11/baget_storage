import argparse
import asyncio
import getpass

from pydantic import ValidationError

from baget_storage.core.config import Settings
from baget_storage.core.exceptions import DomainError
from baget_storage.core.providers.setup import create_container
from baget_storage.domain.use_cases.bootstrap import CreateManagerUseCase, ResetPasswordUseCase
from baget_storage.schemas.bootstrap import ResetPassword
from baget_storage.schemas.user import CreateUser


async def run_command(command: str, email: str, password: str, full_name: str | None) -> None:
    container = create_container(Settings())
    try:
        async with container() as scope:
            if command == "create-manager":
                create = await scope.get(CreateManagerUseCase)
                user = await create.execute(
                    CreateUser(email=email, password=password, full_name=full_name or "")
                )
                print(f"Manager created: {user.email} ({user.id})")
            else:
                reset = await scope.get(ResetPasswordUseCase)
                await reset.execute(ResetPassword(email=email, password=password))
                print("Password reset; existing sessions revoked")
    finally:
        await container.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="baget-storage")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-manager")
    create.add_argument("--email", required=True)
    create.add_argument("--full-name", required=True)
    reset = commands.add_parser("reset-password")
    reset.add_argument("--email", required=True)
    args = parser.parse_args()
    password = getpass.getpass("Password (12-128 characters): ")
    if password != getpass.getpass("Confirm password: "):
        parser.error("Passwords do not match")
    try:
        asyncio.run(
            run_command(args.command, args.email, password, getattr(args, "full_name", None))
        )
    except DomainError as exc:
        parser.exit(1, f"{exc.message}\n")
    except ValidationError:
        parser.exit(1, "Invalid account details or application configuration\n")


if __name__ == "__main__":
    main()
