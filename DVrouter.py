import json
from router import Router
from packet import Packet

class DVrouter(Router):
    def __init__(self, addr, heartbeat_time):
        Router.__init__(self, addr)
        self.heartbeat_time = heartbeat_time   # Thời gian giữa các lần gửi bản cập nhật định kỳ
        self.last_time = 0                     # Lưu thời điểm lần gửi bản cập nhật cuối cùng
        self.routing_table = {addr: (0, None)} # Bảng định tuyến: {địa chỉ đích: (chi phí, cổng)}
        self.neighbors = {}                    # {địa chỉ hàng xóm: (chi phí, cổng)}
        self.neighbors_vector = {}             # {địa chỉ hàng xóm: vector khoảng cách của họ}
        self.INFINITY = 16                     # Chi phí vô hạn, dùng để biểu diễn đường không thể đi được
        self.triggered_update_pending = False  # Đánh dấu nếu cần gửi cập nhật tức thì (triggered update)

    def handle_packet(self, port, packet):
        if packet.is_traceroute:
            # Nếu là gói truy vết, chuyển tiếp nếu có đường đi
            if packet.dst_addr in self.routing_table:
                cost, next_port = self.routing_table[packet.dst_addr]
                if next_port is not None and cost < self.INFINITY:
                    self.send(next_port, packet)
        else:
            # Gói định tuyến từ hàng xóm
            neighbor_addr = packet.src_addr
            vector = json.loads(packet.content)  # Phân tích vector khoảng cách
            old_vector = self.neighbors_vector.get(neighbor_addr, {})
            self.neighbors_vector[neighbor_addr] = vector
            if vector != old_vector:
                # Nếu vector thay đổi, tính lại bảng định tuyến
                updated = self.recompute_routes()
                if updated:
                    self.triggered_update_pending = True

    def handle_new_link(self, port, endpoint, cost):
        # Khi có liên kết mới, thêm hàng xóm và khởi tạo vector
        self.neighbors[endpoint] = (cost, port)
        self.neighbors_vector.setdefault(endpoint, {})
        updated = self.recompute_routes()
        if updated:
            self.triggered_update_pending = True

    def handle_remove_link(self, port):
        # Khi mất liên kết, loại bỏ hàng xóm tương ứng
        neighbor_to_remove = None
        for neighbor, (_, neighbor_port) in self.neighbors.items():
            if neighbor_port == port:
                neighbor_to_remove = neighbor
                break
        if neighbor_to_remove:
            del self.neighbors[neighbor_to_remove]
            self.neighbors_vector.pop(neighbor_to_remove, None)
            # Đặt chi phí của các đích đang đi qua cổng bị mất thành vô hạn
            for dest in self.routing_table:
                if dest != self.addr:
                    cost, next_port = self.routing_table[dest]
                    if next_port == port:
                        self.routing_table[dest] = (self.INFINITY, None)
            updated = self.recompute_routes()
            if updated:
                self.triggered_update_pending = True

    def handle_time(self, time_ms):
        # Gửi bản cập nhật định kỳ nếu đã đến thời gian
        if time_ms - self.last_time >= self.heartbeat_time:
            self.last_time = time_ms
            self.broadcast_distance_vector()
        # Gửi bản cập nhật nếu có thay đổi tức thì
        if self.triggered_update_pending:
            self.broadcast_distance_vector()
            self.triggered_update_pending = False

    def recompute_routes(self):
        # Tính lại bảng định tuyến từ các vector khoảng cách của hàng xóm
        new_table = {self.addr: (0, None)}  # Luôn biết cách đến chính mình
        for neighbor, (cost, port) in self.neighbors.items():
            new_table[neighbor] = (cost, port)  # Đường trực tiếp tới hàng xóm

        # Xem xét vector từ các hàng xóm
        for neighbor_addr, vector in self.neighbors_vector.items():
            if neighbor_addr not in self.neighbors:
                continue
            neighbor_cost, neighbor_port = self.neighbors[neighbor_addr]
            for dest, advertised_cost in vector.items():
                if dest == self.addr:
                    continue
                total_cost = neighbor_cost + advertised_cost
                # Cập nhật nếu:
                # 1. Chưa biết đường tới đích đó
                # 2. Đường mới rẻ hơn
                # 3. Hoặc đi cùng cổng hiện tại (để cập nhật chi phí mới)
                if (dest not in new_table or 
                    total_cost < new_table[dest][0] or
                    (dest in new_table and new_table[dest][1] == neighbor_port)):
                    new_table[dest] = (total_cost, neighbor_port)

        if new_table != self.routing_table:
            self.routing_table = new_table
            return True  # Có thay đổi
        return False

    def broadcast_distance_vector(self):
        # Gửi vector khoảng cách tới tất cả hàng xóm
        for port in self.links.keys():
            neighbor_addr = None
            for addr, (_, neighbor_port) in self.neighbors.items():
                if neighbor_port == port:
                    neighbor_addr = addr
                    break
            vector = {}
            for dest, (cost, next_port) in self.routing_table.items():
                # Split horizon with poisoned reverse: nếu đích đi qua cổng này, quảng bá là vô hạn
                if next_port == port and dest != self.addr:
                    vector[dest] = self.INFINITY
                else:
                    vector[dest] = min(cost, self.INFINITY)
            # Tạo và gửi gói tin định tuyến
            packet = Packet(
                kind=Packet.ROUTING,
                src_addr=self.addr,
                dst_addr=None,
                content=json.dumps(vector)
            )
            self.send(port, packet)

    def __repr__(self):
        # Hiển thị bảng định tuyến (chỉ những đường hợp lệ)
        active_routes = {dest: (cost, port) for dest, (cost, port) in self.routing_table.items() 
                        if cost < self.INFINITY}
        return f"DVrouter(addr={self.addr}, table={active_routes})"
