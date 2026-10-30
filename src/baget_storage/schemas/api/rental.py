from baget_storage.schemas.rental import (
    CreateLease,
    CreateRoom,
    LeaseDTO,
    LeaseFilter,
    RentalPage,
    RoomDTO,
    RoomFilter,
    UpdateRoom,
)


class CreateRoomRequest(CreateRoom):
    pass


class UpdateRoomRequest(UpdateRoom):
    pass


class RoomResponse(RoomDTO):
    pass


class RoomListQuery(RoomFilter):
    pass


class CreateLeaseRequest(CreateLease):
    pass


class LeaseResponse(LeaseDTO):
    pass


class LeaseListQuery(LeaseFilter):
    pass


class RoomPageResponse(RentalPage[RoomResponse]):
    pass


class LeasePageResponse(RentalPage[LeaseResponse]):
    pass
