//casperjs
var casper = require('casper').create();
var url = casper.cli.get(0);

casper.start(url, function() {
    this.echo(this.getHTML());
});
casper.run(function() {
    var _this = this;
    _this.page.close();
    setTimeout(function(){ phantom.exit(0); }, 0);
    phantom.onError = function(){};
    throw new Error('');
});
//casper.run();
