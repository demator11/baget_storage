from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Query, status

from baget_storage.api.auth import CurrentActor
from baget_storage.domain.use_cases.rental import (
    CreateLeaseUseCase,
    CreateRoomUseCase,
    GetLeasesUseCase,
    GetRoomsUseCase,
    GetRoomUseCase,
    TerminateLeaseUseCase,
    UpdateRoomUseCase,
)
from baget_storage.schemas.api.rental import (
    CreateLeaseRequest,
    CreateRoomRequest,
    LeaseListQuery,
    LeasePageResponse,
    LeaseResponse,
    RoomListQuery,
    RoomPageResponse,
    RoomResponse,
    UpdateRoomRequest,
)
from baget_storage.schemas.rental import (
    CreateLease,
    CreateRoom,
    LeaseFilter,
    RoomFilter,
    UpdateRoom,
)

router = APIRouter(tags=["Rental"], route_class=DishkaRoute)


@router.get("/rooms", response_model=RoomPageResponse)
async def get_rooms(
    actor: CurrentActor,
    query: Annotated[RoomListQuery, Query()],
    use_case: FromDishka[GetRoomsUseCase],
) -> RoomPageResponse:
    result = await use_case.execute(actor, RoomFilter(**query.model_dump()))
    return RoomPageResponse.model_validate(result)


@router.post("/rooms", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(
    actor: CurrentActor,
    body: CreateRoomRequest,
    use_case: FromDishka[CreateRoomUseCase],
) -> RoomResponse:
    result = await use_case.execute(actor, CreateRoom(**body.model_dump()))
    return RoomResponse.model_validate(result)


@router.get("/rooms/{room_id}", response_model=RoomResponse)
async def get_room(
    actor: CurrentActor,
    room_id: UUID,
    use_case: FromDishka[GetRoomUseCase],
) -> RoomResponse:
    return RoomResponse.model_validate(await use_case.execute(actor, room_id))


@router.patch("/rooms/{room_id}", response_model=RoomResponse)
async def update_room(
    actor: CurrentActor,
    room_id: UUID,
    body: UpdateRoomRequest,
    use_case: FromDishka[UpdateRoomUseCase],
) -> RoomResponse:
    data = UpdateRoom(**body.model_dump(exclude_unset=True))
    return RoomResponse.model_validate(await use_case.execute(actor, room_id, data))


@router.get("/leases", response_model=LeasePageResponse)
async def get_leases(
    actor: CurrentActor,
    query: Annotated[LeaseListQuery, Query()],
    use_case: FromDishka[GetLeasesUseCase],
) -> LeasePageResponse:
    result = await use_case.execute(actor, LeaseFilter(**query.model_dump()))
    return LeasePageResponse.model_validate(result)


@router.post("/leases", response_model=LeaseResponse, status_code=status.HTTP_201_CREATED)
async def create_lease(
    actor: CurrentActor,
    body: CreateLeaseRequest,
    use_case: FromDishka[CreateLeaseUseCase],
) -> LeaseResponse:
    result = await use_case.execute(actor, CreateLease(**body.model_dump()))
    return LeaseResponse.model_validate(result)


@router.post("/leases/{lease_id}/terminate", response_model=LeaseResponse)
async def terminate_lease(
    actor: CurrentActor,
    lease_id: UUID,
    use_case: FromDishka[TerminateLeaseUseCase],
) -> LeaseResponse:
    return LeaseResponse.model_validate(await use_case.execute(actor, lease_id))
