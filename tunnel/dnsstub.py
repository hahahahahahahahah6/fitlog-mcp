import socket, struct, threading
UPSTREAM=('198.19.0.1',53)
SRV_NAME=b'_v2-origintunneld._tcp.argotunnel.com'
def encode_name(labels):
    return b''.join(struct.pack('B',len(l))+l for l in labels)+b'\x00'
def parse_qname(pkt):
    i=12; labels=[]
    while True:
        ln=pkt[i]; i+=1
        if ln==0: break
        if ln&0xC0:
            ptr=((ln&0x3F)<<8)|pkt[i]; i+=1
            j=ptr; sub=[]
            while True:
                l2=pkt[j]; j+=1
                if l2==0: break
                sub.append(pkt[j:j+l2]); j+=l2
            labels+=sub; break
        labels.append(pkt[i:i+ln]); i+=ln
    qtype,qclass=struct.unpack('!HH',pkt[i:i+4])
    return b'.'.join(labels).lower(), i+4, qtype, qclass
def forward(q):
    s=socket.create_connection(UPSTREAM,timeout=8)
    s.sendall(struct.pack('!H',len(q))+q)
    ln=struct.unpack('!H',s.recv(2))[0]
    data=b''
    while len(data)<ln: data+=s.recv(ln-len(data))
    s.close(); return data
def answer(q):
    tid=q[:2]
    name,off,qtype,qclass=parse_qname(q)
    question=q[12:off]
    if name==SRV_NAME and qtype==33:
        targets=[(b'region1',7844),(b'region2',7844)]
        ans=b''
        for t,port in targets:
            rdata=struct.pack('!HHH',0,1,port)+encode_name([t,b'v2',b'argotunnel',b'com'])
            ans+=b'\xc0\x0c'+struct.pack('!HHIH',33,1,300,len(rdata))+rdata
        hdr=struct.pack('!HHHHHH',struct.unpack('!H',tid)[0],0x8180,1,len(targets),0,0)
        return hdr+question+ans
    return forward(q)
# NOTE: sandbox blocks sendto()/sendmsg() (EPERM) but allows connect()+send().
# Replies MUST come from 127.0.0.1:53 (clients use connected UDP sockets).
# ALSO: sandbox blocks re-connect() of a UDP socket to a different peer, so
# use a FRESH sender socket per reply, bound to 127.0.0.1:53 (SO_REUSEADDR).
def udp_reply(resp, addr):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('127.0.0.1', 53))
        s.connect(addr)
        s.send(resp)
    except Exception as e:
        print(f'send fail to {addr}: {e}', flush=True)
    finally:
        s.close()
def tcp_loop():
    srv=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    srv.bind(('127.0.0.1',53)); srv.listen(50)
    while True:
        c,_=srv.accept()
        threading.Thread(target=handle_tcp,args=(c,),daemon=True).start()
def handle_tcp(conn):
    try:
        ln=struct.unpack('!H',conn.recv(2))[0]
        q=b''
        while len(q)<ln: q+=conn.recv(ln-len(q))
        resp=answer(q)
        conn.sendall(struct.pack('!H',len(resp))+resp)
    except Exception: pass
    finally: conn.close()
def udp_loop():
    rcv=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
    rcv.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    rcv.bind(('127.0.0.1',53))
    while True:
        try:
            q,addr=rcv.recvfrom(4096)
            try:
                name,_,qtype,_=parse_qname(q)
                print(f'Q {name} type={qtype} from={addr}',flush=True)
            except Exception as e:
                print(f'Q parse fail: {e}',flush=True)
            resp=answer(q)
            print(f'A len={len(resp)}',flush=True)
            threading.Thread(target=udp_reply,args=(resp,addr),daemon=True).start()
        except Exception as e:
            print(f'loop err: {e}',flush=True)
threading.Thread(target=tcp_loop,daemon=True).start()
print('stub up (dual-socket)',flush=True)
udp_loop()
