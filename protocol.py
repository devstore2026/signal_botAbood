"""Small Engine.IO/Socket.IO event decoder; supports JSON binary attachments.
Unsupported non-JSON binary data is ignored, never interpreted as prices.
"""
import json
import re

class Decoder:
    def __init__(self):
        self.reset()
    def reset(self):
        self.pending=None
        self.parts=[]
        self.count=0
    def _replace(self, node):
        if isinstance(node,dict):
            if node.get('_placeholder') is True:
                idx=node.get('num')
                if not isinstance(idx,int) or not 0<=idx<len(self.parts):
                    raise ValueError('bad placeholder')
                return self.parts[idx]
            return {k:self._replace(v) for k,v in node.items()}
        if isinstance(node,list):
            return [self._replace(v) for v in node]
        return node
    def feed(self, frame):
        if isinstance(frame,bytes):
            try:
                data=json.loads(frame.decode('utf-8'))
            except (UnicodeDecodeError,ValueError):
                self.reset()
                return []
            if self.pending is None:
                return []  # no event context: never hunt for arbitrary numbers
            self.parts.append(data)
            if len(self.parts)<self.count:
                return []
            try:
                result=self._replace(self.pending)
            except ValueError:
                self.reset()
                return []
            self.reset()
            return [result]
        if not isinstance(frame,str):
            return []
        if frame.startswith('45'):
            match=re.match(r'^45(\d+)-',frame)
            if not match or not 1<=int(match[1])<=8:
                self.reset()
                return []
            self.reset()
            start=frame.find('[',match.end())
            try:
                self.pending=json.loads(frame[start:]) if start>=0 else None
            except ValueError:
                self.reset()
                return []
            self.count=int(match[1])
            return []
        if frame.startswith('42'):
            self.reset()
            try:
                start=frame.index('[')
                data=json.loads(frame[start:])
                return [data] if isinstance(data,list) else []
            except (ValueError,TypeError):
                return []
        return []
