import json
from router import Router
from packet import Packet

class DVrouter(Router):
    def __init__(self, addr, heartbeat_time):
        Router.__init__(self, addr)
        self.heartbeat_time = heartbeat_time
        self.last_time = 0
        self.routing_table = {addr: (0, None)}
        self.neighbors = {}
        self.neighbors_vector = {}
        self.INFINITY = 16
        self.triggered_update_pending = False

    def handle_packet(self, port, packet):
        if packet.is_traceroute:
            if packet.dst_addr in self.routing_table:
                cost, next_port = self.routing_table[packet.dst_addr]
                if next_port is not None and cost < self.INFINITY:
                    self.send(next_port, packet)
        else:
            neighbor_addr = packet.src_addr
            vector = json.loads(packet.content)
            old_vector = self.neighbors_vector.get(neighbor_addr, {})
            self.neighbors_vector[neighbor_addr] = vector
            if vector != old_vector:
                updated = self.recompute_routes()
                if updated:
                    self.triggered_update_pending = True

    def handle_new_link(self, port, endpoint, cost):
        self.neighbors[endpoint] = (cost, port)
        self.neighbors_vector.setdefault(endpoint, {})
        updated = self.recompute_routes()
        if updated:
            self.triggered_update_pending = True

    def handle_remove_link(self, port):
        neighbor_to_remove = None
        for neighbor, (_, neighbor_port) in self.neighbors.items():
            if neighbor_port == port:
                neighbor_to_remove = neighbor
                break
        if neighbor_to_remove:
            del self.neighbors[neighbor_to_remove]
            self.neighbors_vector.pop(neighbor_to_remove, None)
            for dest in self.routing_table:
                if dest != self.addr:
                    cost, next_port = self.routing_table[dest]
                    if next_port == port:
                        self.routing_table[dest] = (self.INFINITY, None)
            updated = self.recompute_routes()
            if updated:
                self.triggered_update_pending = True

    def handle_time(self, time_ms):
        if time_ms - self.last_time >= self.heartbeat_time:
            self.last_time = time_ms
            self.broadcast_distance_vector()
        if self.triggered_update_pending:
            self.broadcast_distance_vector()
            self.triggered_update_pending = False

    def recompute_routes(self):
        new_table = {self.addr: (0, None)}
        for neighbor, (cost, port) in self.neighbors.items():
            new_table[neighbor] = (cost, port)
        for neighbor_addr, vector in self.neighbors_vector.items():
            if neighbor_addr not in self.neighbors:
                continue
            neighbor_cost, neighbor_port = self.neighbors[neighbor_addr]
            for dest, advertised_cost in vector.items():
                if dest == self.addr:
                    continue
                total_cost = neighbor_cost + advertised_cost
                if (dest not in new_table or 
                    total_cost < new_table[dest][0] or
                    (dest in new_table and new_table[dest][1] == neighbor_port)):
                    new_table[dest] = (total_cost, neighbor_port)
        if new_table != self.routing_table:
            self.routing_table = new_table
            return True
        return False

    def broadcast_distance_vector(self):
        for port in self.links.keys():
            neighbor_addr = None
            for addr, (_, neighbor_port) in self.neighbors.items():
                if neighbor_port == port:
                    neighbor_addr = addr
                    break
            vector = {}
            for dest, (cost, next_port) in self.routing_table.items():
                if next_port == port and dest != self.addr:
                    vector[dest] = self.INFINITY
                else:
                    vector[dest] = min(cost, self.INFINITY)
            packet = Packet(
                kind=Packet.ROUTING,
                src_addr=self.addr,
                dst_addr=None,
                content=json.dumps(vector)
            )
            self.send(port, packet)

    def __repr__(self):
        active_routes = {dest: (cost, port) for dest, (cost, port) in self.routing_table.items() 
                        if cost < self.INFINITY}
        return f"DVrouter(addr={self.addr}, table={active_routes})"
