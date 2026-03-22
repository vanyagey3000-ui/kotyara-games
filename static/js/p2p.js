// ═══════════════════════════════════════════════════════════
// KotyaraGames — P2P через WebRTC
// Сервер только для сигналинга (поиск + обмен SDP/ICE)
// Игровые данные идут напрямую между браузерами
// ═══════════════════════════════════════════════════════════

var P2P = {
    conn: null,         // RTCPeerConnection
    channel: null,      // RTCDataChannel
    isHost: false,
    connected: false,
    onMessage: null,    // callback
    onConnect: null,
    onDisconnect: null,

    // Бесплатные STUN серверы (помогают пробить NAT)
    config: {
        iceServers: [
            { urls: 'stun:stun.l.google.com:19302' },
            { urls: 'stun:stun1.l.google.com:19302' },
            { urls: 'stun:stun2.l.google.com:19302' },
            { urls: 'stun:stun3.l.google.com:19302' },
            { urls: 'stun:stun.stunprotocol.org:3478' }
        ]
    },

    init: function(socket, roomId, isHost) {
        var self = this;
        this.isHost = isHost;
        this.connected = false;

        console.log('P2P: init, host=' + isHost);

        // Создаём соединение
        this.conn = new RTCPeerConnection(this.config);

        // ICE кандидаты — отправляем через сервер
        this.conn.onicecandidate = function(e) {
            if (e.candidate) {
                socket.emit('p2p_signal', {
                    room_id: roomId,
                    type: 'ice',
                    data: e.candidate
                });
            }
        };

        this.conn.onconnectionstatechange = function() {
            console.log('P2P state:', self.conn.connectionState);
            if (self.conn.connectionState === 'disconnected' ||
                self.conn.connectionState === 'failed') {
                self.connected = false;
                if (self.onDisconnect) self.onDisconnect();
            }
        };

        if (isHost) {
            // Хост создаёт data channel
            this.channel = this.conn.createDataChannel('game', {
                ordered: false,       // UDP-подобно — быстрее
                maxRetransmits: 0     // Не ретрансмитить — скорость важнее
            });
            this.setupChannel(this.channel);

            // Создаём offer
            this.conn.createOffer().then(function(offer) {
                return self.conn.setLocalDescription(offer);
            }).then(function() {
                socket.emit('p2p_signal', {
                    room_id: roomId,
                    type: 'offer',
                    data: self.conn.localDescription
                });
                console.log('P2P: offer sent');
            });
        } else {
            // Гость ждёт data channel
            this.conn.ondatachannel = function(e) {
                self.channel = e.channel;
                self.setupChannel(self.channel);
            };
        }

        // Принимаем сигналы от другого игрока
        socket.on('p2p_signal', function(data) {
            self.handleSignal(socket, roomId, data);
        });
    },

    setupChannel: function(ch) {
        var self = this;

        ch.onopen = function() {
            console.log('P2P: channel OPEN!');
            self.connected = true;
            if (self.onConnect) self.onConnect();
        };

        ch.onclose = function() {
            console.log('P2P: channel closed');
            self.connected = false;
            if (self.onDisconnect) self.onDisconnect();
        };

        ch.onmessage = function(e) {
            if (self.onMessage) {
                try {
                    var msg = JSON.parse(e.data);
                    self.onMessage(msg);
                } catch(err) {}
            }
        };
    },

    handleSignal: function(socket, roomId, data) {
        var self = this;

        if (data.type === 'offer' && !this.isHost) {
            // Гость получил offer
            this.conn.setRemoteDescription(new RTCSessionDescription(data.data))
            .then(function() {
                return self.conn.createAnswer();
            }).then(function(answer) {
                return self.conn.setLocalDescription(answer);
            }).then(function() {
                socket.emit('p2p_signal', {
                    room_id: roomId,
                    type: 'answer',
                    data: self.conn.localDescription
                });
                console.log('P2P: answer sent');
            });
        }
        else if (data.type === 'answer' && this.isHost) {
            // Хост получил answer
            this.conn.setRemoteDescription(new RTCSessionDescription(data.data));
            console.log('P2P: answer received');
        }
        else if (data.type === 'ice') {
            this.conn.addIceCandidate(new RTCIceCandidate(data.data));
        }
    },

    send: function(msg) {
        if (this.channel && this.channel.readyState === 'open') {
            this.channel.send(JSON.stringify(msg));
        }
    },

    close: function() {
        if (this.channel) this.channel.close();
        if (this.conn) this.conn.close();
        this.connected = false;
    }
};